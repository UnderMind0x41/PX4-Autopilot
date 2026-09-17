#!/usr/bin/env bash
# Interactive frontend for the repository's PX4 and NuttX build targets.
set -uo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd) || exit 1
LOCAL_ROOT=$ROOT
SOURCE_REF=current SOURCE_COMMIT='' PROFILE='' LIST_TARGETS=0 LIST_REFS=0
AUTOTUNE=''
if ((BASH_VERSINFO[0] < 4)); then
	printf 'Требуется Bash 4 или новее.\n' >&2
	exit 1
fi

declare -A CONFIGS=()
for file in "$ROOT"/boards/*/*/*.px4board; do
	[[ -f $file ]] || continue
	name=${file#"$ROOT/boards/"}
	name=${name%.px4board}
	CONFIGS[${name//\//_}]=$file
done

TARGET=px4_sitl_default
JOBS=$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf '4')
# Keep the default modest on workstations also running a simulator.
((JOBS > 8)) && JOBS=8
BUILD_TYPE=RelWithDebInfo
ACTION='' SIMULATOR=gz_x500 UI=text DRY_RUN=0 FORCE_TEXT=0
FIRMWARE='' PORT='' INTERACTIVE=0
UPLOAD_METHOD=auto DFU_SERIAL=''
STATE="$ROOT/build/.build-sh"
if [[ -f $STATE/target ]]; then
	IFS= read -r saved < "$STATE/target" || :
	[[ -n ${CONFIGS[${saved:-invalid}]:-} ]] && TARGET=$saved
fi

usage() {
	cat <<'EOF'
PX4 Build — сборка и настройка прошивок в терминале

  ./scripts/build.sh                         интерактивное меню
  ./scripts/build.sh --text                  меню без whiptail
  ./scripts/build.sh --list-targets          доступные конфигурации
  ./scripts/build.sh --target px4_sitl_default --action build
  ./scripts/build.sh --ref v1.16.0 --target holybro_kakuteh7_default --action build
  ./scripts/build.sh --target holybro_kakuteh7_default --action save-config --profile my-config
  ./scripts/build.sh --target holybro_kakuteh7_default --action load-config --profile my-config
  ./scripts/build.sh --target holybro_kakuteh7dualimu_default --action px4-config
  ./scripts/build.sh --target holybro_kakuteh7_default --action autotune-config --autotune experimental
  ./scripts/build.sh --target holybro_kakuteh7dualimu_default --action kernel-config
  ./scripts/build.sh --target px4_sitl_default --action run --sim gz_x500
  ./scripts/build.sh --target holybro_kakuteh7_default --action build-upload
  ./scripts/build.sh --action upload --firmware /path/to/firmware.px4
  ./scripts/build.sh --action upload --method dfu --firmware /path/to/bootloader.bin

Опции:
  --target NAME       полное имя из --list-targets
  --ref REF           current (по умолчанию), локальная/удалённая ветка, тег или SHA
  --list-refs         текущая ветка, известные локально ветки и теги
  --action ACTION     build, build-upload, upload, px4-config, kernel-config, clean, run, info,
                      doctor, python-setup, save-config, load-config, list-configs, autotune-config
  --autotune MODE     standard, experimental, disabled для --action autotune-config
  --profile NAME      имя профиля для save-config / load-config
  --firmware FILE     существующий .px4 или .bin для upload (без сборки)
  --method METHOD     auto (по умолчанию), serial или dfu
  --dfu-serial SERIAL серийный номер DFU, обязателен при нескольких платах
  --port PORT         порт загрузчика, например /dev/ttyACM0; по умолчанию авто
  --jobs N            количество параллельных задач
  --build-type TYPE   RelWithDebInfo (по умолчанию), Release, Debug, MinSizeRel
  --sim NAME          gz_x500, jmavsim, sihsim_quadx и другие цели SITL
  --dry-run           показать команду без выполнения и записи файлов
  --text              обычное текстовое меню вместо whiptail
  --help              эта справка

Сборка выполняется на текущем компьютере штатной командой make PX4.
current собирает рабочий каталог: локальные коммиты и незакоммиченные изменения.
Другие refs используют отдельный worktree в build/.build-sh/sources/<SHA>.
Его настройки и результаты сохраняются между запусками; подмодули загружаются
перед первой сборкой. Основная ветка при этом не переключается.
Ветки и теги берутся из локального Git; обновить список: git fetch --all --tags.
Профили: boards/<производитель>/<плата>/custom-configs/<вариант>/<имя>/
в исходном репозитории. В профиле — PX4 .px4board и NuttX defconfig (если есть).
Загрузка заменяет конфиги выбранного исходного каталога с резервной копией;
существующий профиль не перезаписывается — используйте новое имя.
Python: PX4_PYTHON, затем .venv выбранного или основного каталога, затем python3 из PATH.
PX4-конфигуратор сохраняет boards/.../*.px4board; NuttX — nuttx-config/.../defconfig.
Перед открытием конфигуратора исходный файл копируется в build/.build-sh/backups.
Автотюн: experimental заменяет стандартный алгоритм проверкой отклика;
требует дополнительной Flash и примерно 35 КиБ heap. Настройка сохраняется
в выбранном .px4board с резервной копией; затем выполните build.
SITL работает без ядра NuttX. Сборка и запуск SITL — отдельные действия.
Результаты каждой сборки с SHA256: build/.build-sh/artifacts/<target>/<операция>.
Перед сборкой прежние конечные файлы сохраняются в previous внутри этой операции;
они создаются заново, промежуточные объектные файлы используются повторно.
Журналы — build/.build-sh/logs. Поиск target при вводе требует fzf (apt install fzf).
Auto выбирает dfu-util, если подключена STM32 DFU; иначе — PX4 serial uploader.
DFU требует .bin и одноимённый .elf из одной сборки; для .px4 проверяется и пакет.
Адрес берётся из ELF. После записи Flash читается обратно и сравнивается с .bin.
Bootloader устанавливается только через DFU. Serial поддерживает обычные .px4.
EOF
}

die() { printf 'Ошибка: %s\n' "$*" >&2; exit 1; }
while (($#)); do
	case $1 in
		--target|--action|--jobs|--build-type|--sim|--firmware|--port|--method|--dfu-serial|--ref|--profile|--autotune)
			(($# >= 2)) || die "Для $1 требуется значение"
			case $1 in
				--target) TARGET=$2 ;; --action) ACTION=$2 ;; --jobs) JOBS=$2 ;;
				--build-type) BUILD_TYPE=$2 ;; --sim) SIMULATOR=$2 ;;
				--firmware) FIRMWARE=$2 ;; --port) PORT=$2 ;;
				--method) UPLOAD_METHOD=$2 ;; --dfu-serial) DFU_SERIAL=$2 ;;
				--ref) SOURCE_REF=$2 ;; --profile) PROFILE=$2 ;;
				--autotune) AUTOTUNE=$2 ;;
			esac
			shift 2 ;;
		--text) FORCE_TEXT=1; shift ;;
		--dry-run) DRY_RUN=1; shift ;;
		--list-targets) LIST_TARGETS=1; shift ;;
		--list-refs) LIST_REFS=1; shift ;;
		--help|-h) usage; exit 0 ;;
		*) die "Неизвестная опция: $1 (см. --help)" ;;
	esac
done
[[ -n $TARGET ]] || die 'Имя конфигурации не может быть пустым'
[[ $JOBS =~ ^[1-9][0-9]*$ && ${#JOBS} -le 4 ]] || die '--jobs: число от 1 до 9999'
case $BUILD_TYPE in RelWithDebInfo|Release|Debug|MinSizeRel) ;; *) die 'Неизвестный тип сборки' ;; esac
case $ACTION in ''|build|build-upload|upload|px4-config|kernel-config|clean|run|info|doctor|python-setup|save-config|load-config|list-configs|autotune-config) ;; *) die 'Неизвестное действие' ;; esac
case $AUTOTUNE in ''|standard|experimental|disabled) ;; *) die '--autotune: standard, experimental или disabled' ;; esac
[[ -z $AUTOTUNE || $ACTION == autotune-config ]] || die '--autotune применяется только к --action autotune-config'
[[ $ACTION != autotune-config || -n $AUTOTUNE ]] || die 'Укажите --autotune standard, experimental или disabled'
[[ -z $PROFILE || $ACTION == save-config || $ACTION == load-config ]] || die '--profile применяется только к save-config / load-config'
[[ -z $FIRMWARE || $ACTION == upload ]] || die '--firmware применяется только к --action upload'
case $UPLOAD_METHOD in auto|serial|dfu) ;; *) die '--method: auto, serial или dfu' ;; esac
[[ -z $PORT || $UPLOAD_METHOD != dfu ]] || die 'DFU использует --dfu-serial, а не --port'

# shellcheck source=scripts/build-configs.sh
source "$LOCAL_ROOT/scripts/build-configs.sh"

properties() {
	BOARD_DIR=${CONFIGS[$TARGET]%/*}
	VARIANT=${CONFIGS[$TARGET]##*/}
	VARIANT=${VARIANT%.px4board}
	BUILD_DIR="$ROOT/build/$TARGET"
	PROFILE_DIR="$LOCAL_ROOT/${BOARD_DIR#"$ROOT/"}/custom-configs/$VARIANT"
	NUTTX_DEFCONFIG=
	if source_has "${BOARD_DIR#"$ROOT/"}/nuttx-config"; then
		local kernel=nsh
		if source_has "${BOARD_DIR#"$ROOT/"}/nuttx-config/$VARIANT"; then
			kernel=$VARIANT
		elif [[ $VARIANT == bootloader* ]] && source_has "${BOARD_DIR#"$ROOT/"}/nuttx-config/bootloader"; then
			kernel=bootloader
		fi
		NUTTX_DEFCONFIG="$BOARD_DIR/nuttx-config/$kernel/defconfig"
	fi
	PYTHON=${PX4_PYTHON:-}
	if [[ -z $PYTHON ]]; then
		if [[ -x $ROOT/.venv/bin/python ]]; then PYTHON="$ROOT/.venv/bin/python"
		elif [[ -x $LOCAL_ROOT/.venv/bin/python ]]; then PYTHON="$LOCAL_ROOT/.venv/bin/python"
		else PYTHON=$(command -v python3 || :); fi
	fi
	TOOLCHAIN=
	local line config resolved
	if [[ -n $PYTHON ]] && resolved=$(command -v "$PYTHON" 2>/dev/null); then
		PYTHON="$(cd -- "$(dirname -- "$resolved")" && pwd)/${resolved##*/}"
	fi
	for config in "$BOARD_DIR/default.px4board" "${CONFIGS[$TARGET]}"; do
		source_has "${config#"$ROOT/"}" || continue
		while IFS= read -r line; do
			case $line in CONFIG_BOARD_TOOLCHAIN=*) TOOLCHAIN=${line#*=}; TOOLCHAIN=${TOOLCHAIN//\"/} ;; esac
		done < <(source_read "${config#"$ROOT/"}")
	done
}

# Output only the selected tag on stdout; all UI rendering goes to stderr.
choose() {
	local title=$1 prompt=$2 answer i
	shift 2
	if [[ $UI == whiptail ]]; then
		whiptail --title "$title" --cancel-button 'Назад' --menu "$prompt" \
			"$HEIGHT" "$WIDTH" "$((HEIGHT - 13))" "$@" 3>&1 1>&2 2>&3
		return $?
	fi
	local -a entries=("$@")
	printf '\n%s\n%s\n' "$title" "$prompt" >&2
	for ((i=0; i<${#entries[@]}; i+=2)); do
		printf '  %d) %s — %s\n' "$((i / 2 + 1))" "${entries[i]}" "${entries[i+1]}" >&2
	done
	printf '  0) Назад / выход\nВыбор: ' >&2
	while IFS= read -r answer; do
		[[ $answer == 0 || -z $answer ]] && return 1
		if [[ $answer =~ ^[1-9][0-9]*$ && ${#answer} -lt 5 ]] && ((answer <= ${#entries[@]} / 2)); then
			printf '%s\n' "${entries[(answer - 1) * 2]}"; return 0
		fi
		printf 'Введите номер пункта: ' >&2
	done
	return 1
}

pause() { local answer; printf '\nEnter — вернуться в меню… '; IFS= read -r answer || :; }

filter_list() {
	command -v fzf >/dev/null || { printf 'Для поиска установите fzf: sudo apt install fzf\n' >&2; return 1; }
	FZF_DEFAULT_OPTS='' FZF_DEFAULT_OPTS_FILE=/dev/null fzf --layout=reverse --border \
		--prompt="$1 > " --header='Печатайте для фильтрации • ↑↓ выбор • Enter принять • Esc назад'
}

select_target() {
	local selected
	selected=$(printf '%s\n' "${!CONFIGS[@]}" | LC_ALL=C sort | filter_list 'Target') || return
	[[ -n ${CONFIGS[$selected]:-} ]] || return 1
	TARGET=$selected
	if (( ! DRY_RUN )); then
		mkdir -p "$STATE" && printf '%s\n' "$TARGET" > "$STATE/target"
	fi
	properties
}

doctor() {
	local missing=0 tool
	printf 'Цель: %s\nPython: %s\n' "$TARGET" "${PYTHON:-не найден}"
	for tool in git make cmake c++ flock sha256sum; do
		if command -v "$tool" >/dev/null; then printf '  OK  %s\n' "$tool"
		else printf '  НЕТ %s\n' "$tool"; missing=1; fi
	done
	if [[ -n $TOOLCHAIN ]]; then
		if command -v "${TOOLCHAIN}-gcc" >/dev/null; then printf '  OK  %s-gcc\n' "$TOOLCHAIN"
		else printf '  НЕТ %s-gcc — нужен toolchain этой платы\n' "$TOOLCHAIN"; missing=1; fi
	fi
	if [[ -n $PYTHON ]] && "$PYTHON" -c 'import menuconfig, jinja2, em, yaml, jsonschema' 2>/dev/null; then
		printf '  OK  основные Python-зависимости\n'
	else
		printf '  НЕТ Python-зависимости — используйте пункт «Подготовить Python»\n'; missing=1
	fi
	if [[ $ACTION == kernel-config ]] && ! command -v kconfig-mconf >/dev/null; then
		printf '  НЕТ kconfig-mconf (пакет kconfig-frontends для NuttX)\n'; missing=1
	fi
	printf '\nПолная настройка окружения: Tools/setup/ubuntu.sh и docs/en/dev_setup/\n'
	return "$missing"
}

info() {
	printf '%s\nВыбрано: %s\nИсходники: %s\n' "$(current_source_label)" "$SOURCE_REF" "$ROOT"
	[[ -z $SOURCE_COMMIT ]] || printf 'Commit: %s\n' "$SOURCE_COMMIT"
	printf 'Цель: %s\nТип сборки: %s; задач: %s\nPython: %s\n' "$TARGET" "$BUILD_TYPE" "$JOBS" "${PYTHON:-не найден}"
	printf 'PX4: %s\nЯдро: %s\nСборка: %s\n' "${CONFIGS[$TARGET]}" "${NUTTX_DEFCONFIG:-без NuttX}" "$BUILD_DIR"
	if autotune_available; then printf 'Автотюн: %s\n' "$(autotune_mode)"; fi
	printf '\nАрхивы операций: %s/artifacts/%s\n' "$STATE" "$TARGET"
	printf 'Успешные сборки отмечены SUCCESS; состав и SHA256 записаны в SHA256SUMS.\n'
	printf '\nЖурналы: %s/logs\nРезервные копии конфигураций: %s/backups\n' "$STATE" "$STATE"
	printf 'Кастомные конфигурации: %s\n' "$PROFILE_DIR"
}

run_command() {
	local terminal=$1 status log
	shift
	printf '\nКоманда: '; printf '%q ' "$@"; printf '\n\n'
	((DRY_RUN)) && return 0
	if ((terminal)); then
		"$@"; status=$?
	else
		mkdir -p "$STATE/logs" || return 1
		log=$(mktemp "$STATE/logs/${TARGET}-${ACTION}-$(date +%Y%m%d-%H%M%S)-XXXXXX.log") || return 1
		local -a results
		"$@" 2>&1 | tee "$log"
		results=("${PIPESTATUS[@]}")
		status=${results[0]}
		((status == 0)) && status=${results[1]}
		printf '\nЖурнал: %s\n' "$log"
	fi
	if ((status == 0)); then printf '\nГотово.\n'
	else printf '\nКоманда завершилась с кодом %s.\n' "$status" >&2; fi
	return "$status"
}

# Called under the operation lock. Only final products are moved, so make can
# reuse object files but cannot report a leftover image as a new result.
prepare_artifacts() {
	local file
	mkdir -p "$STATE/artifacts/$TARGET" || return 1
	RESULT_DIR=$(mktemp -d "$STATE/artifacts/$TARGET/$(date +%Y%m%d-%H%M%S)-XXXXXX") || return 1
	mkdir "$RESULT_DIR/previous" || return 1
	printf 'Архив этой операции: %s\n' "$RESULT_DIR"
	PRODUCTS=("$TARGET.elf" "$TARGET.bin" "$TARGET.px4" "$TARGET.hex" "${TARGET}_signed.bin" "${TARGET}_unsigned.bin")
	[[ -n $NUTTX_DEFCONFIG ]] || PRODUCTS=(bin/px4)
	for file in "${PRODUCTS[@]}"; do
		if [[ -e $BUILD_DIR/$file ]]; then
			mv -- "$BUILD_DIR/$file" "$RESULT_DIR/previous/${file##*/}" || return 1
		fi
	done
}

collect_artifacts() {
	local file primary="$TARGET.elf"
	local -a collected=()
	[[ -n $NUTTX_DEFCONFIG ]] || primary=bin/px4
	[[ -s $BUILD_DIR/$primary ]] || {
		printf 'Сборка не создала %s. Успешный результат не зарегистрирован.\n' "$primary" >&2; return 1;
	}
	for file in "${PRODUCTS[@]}"; do
		[[ -s $BUILD_DIR/$file ]] || continue
		cp -p -- "$BUILD_DIR/$file" "$RESULT_DIR/${file##*/}" || return 1
		collected+=("${file##*/}")
	done
	(
		cd -- "$RESULT_DIR" || exit 1
		sha256sum -- "${collected[@]}" > SHA256SUMS || exit 1
		{
			printf 'Target: %s\nBuild type: %s\nFinished: %s\n' "$TARGET" "$BUILD_TYPE" "$(date -Iseconds)"
			printf 'Git HEAD: %s\n' "$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || printf unknown)"
			printf 'Source ref: %s\nSource directory: %s\n' "$SOURCE_REF" "$ROOT"
			printf '\nWorking tree at completion:\n'
			git -C "$ROOT" status --short 2>/dev/null || :
		} > SUCCESS
	) || return 1
	printf '\nФайлы текущей успешной сборки:\n'
	printf '  %s\n' "${collected[@]/#/$RESULT_DIR/}"
	printf 'Состав и контрольные суммы: %s/SHA256SUMS\n' "$RESULT_DIR"
}

input_path() {
	if [[ $UI == whiptail ]]; then
		whiptail --title "$1" --inputbox "$2" 10 "$WIDTH" '' 3>&1 1>&2 2>&3
	else
		local answer
		printf '%s (пусто — назад): ' "$2" >&2
		IFS= read -r answer && [[ -n $answer ]] && printf '%s\n' "$answer"
	fi
}

select_firmware() {
	local mode
	mode=$(choose 'Загрузка прошивки' 'Сборка не будет запускаться' archive 'Выбрать имеющийся .px4 / .bin' path 'Ввести путь к файлу') || return
	if [[ $mode == path ]]; then
		FIRMWARE=$(input_path 'Прошивка' 'Путь к файлу .px4 или .bin')
	else
		FIRMWARE=$({
			find "$ROOT/build" -maxdepth 2 -type f \( -name '*.px4' -o -name '*.bin' \) -print 2>/dev/null
			find "$STATE/artifacts" -type f \( -name '*.px4' -o -name '*.bin' \) -print 2>/dev/null
		} | LC_ALL=C sort -r | filter_list 'Прошивка')
	fi
	[[ -n $FIRMWARE ]]
}

dfu_devices() {
	local listing line pattern='serial="([^"]+)"'
	command -v dfu-util >/dev/null || return 0
	listing=$(dfu-util -d 0483:df11 -l 2>&1) || { printf '%s\n' "$listing" >&2; return 1; }
	while IFS= read -r line; do
		if [[ $line == *'alt=0,'* && $line == *'@Internal Flash'* && $line =~ $pattern ]]; then
			printf '%s\n' "${BASH_REMATCH[1]}"
		fi
	done <<< "$listing"
}

# A raw binary has no address. Require its matching ARM ELF and check every
# load segment, rather than guessing an address from the currently selected target.
dfu_image_address() {
	"$PYTHON" - "$FIRMWARE" <<'PY'
import base64
import json
from pathlib import Path
import struct
import sys
import zlib

try:
    source = Path(sys.argv[1])
    binary = source.with_suffix('.bin').read_bytes()
    elf = source.with_suffix('.elf').read_bytes()
    if elf[:6] != b'\x7fELF\x01\x01' or struct.unpack_from('<H', elf, 18)[0] != 40:
        raise ValueError('DFU поддерживает STM32 с ELF32 little-endian ARM')
    offset = struct.unpack_from('<I', elf, 28)[0]
    stride, count = struct.unpack_from('<HH', elf, 42)
    segments = []
    for index in range(count):
        kind, pos, _, address, size, _, _, _ = struct.unpack_from('<8I', elf, offset + index * stride)
        if kind == 1 and size:
            if not (0x08000000 <= address < address + size <= 0x09000000) or pos + size > len(elf):
                raise ValueError('ELF содержит сегмент вне внутренней Flash STM32')
            segments.append((address, elf[pos:pos + size]))
    start = min(address for address, _ in segments)
    end = max(address + len(data) for address, data in segments)
    expected = bytearray(end - start)
    for address, data in segments:
        expected[address - start:address - start + len(data)] = data
    if not binary or binary != expected:
        raise ValueError('.bin не соответствует .elf; выберите файлы одной сборки')
    if source.suffix == '.px4':
        package = json.loads(source.read_text())
        if package['magic'] != 'PX4FWv1' or zlib.decompress(base64.b64decode(package['image'])) != binary:
            raise ValueError('.px4 не соответствует .bin')
    print(f'0x{start:08x}')
except (OSError, ValueError, KeyError, struct.error, zlib.error) as error:
    sys.exit(f'Ошибка подготовки DFU: {error}')
PY
}

dfu_leave() {
	local output status devices
	output=$("$@" 2>&1); status=$?
	printf '%s\n' "$output"
	# Some STM32 ROMs disconnect before the final GETSTATUS. Accept EX_IOERR
	# only at this post-verification step, after the leave request was sent and
	# the selected device actually disappeared from DFU.
	if ((status == 74)) && [[ $output == *'Submitting leave request...'* ]]; then
		devices=$(dfu_devices) || return "$status"
		if [[ $'\n'$devices$'\n' != *$'\n'"$DFU_SERIAL"$'\n'* ]]; then
			printf 'Flash проверена; устройство отключило DFU после команды запуска.\n'
			return 0
		fi
	fi
	return "$status"
}

upload_dfu() (
	local address binary="${FIRMWARE%.*}.bin" operation readback size answer
	command -v dfu-util >/dev/null || { printf 'Установите dfu-util.\n' >&2; exit 1; }
	address=$(dfu_image_address) || exit 1
	printf '\nDFU: %s\nФайл: %s\nАдрес из ELF: %s\n' "$DFU_SERIAL" "$binary" "$address"
	if ((INTERACTIVE)); then
		answer=$(choose 'Загрузить через STM32 DFU?' "$binary
USB serial: $DFU_SERIAL; адрес: $address.
DFU не определяет модель платы: файл должен соответствовать подключённой плате." no 'Назад' yes 'Записать и проверить Flash') || exit 0
		[[ $answer == yes ]] || exit 0
	fi
	local -a command=(dfu-util -d 0483:df11 -S "$DFU_SERIAL" -a 0)
	if ((DRY_RUN)); then
		run_command 0 "${command[@]}" -s "$address" -D "$binary"
		printf 'Затем: чтение Flash, побайтовая проверка и выход из DFU.\n'
		exit 0
	fi
	mkdir -p "$STATE/flashes" || exit 1
	operation=$(mktemp -d "$STATE/flashes/$(date +%Y%m%d-%H%M%S)-XXXXXX") || exit 1
	readback="$operation/readback.bin"
	cp -- "$binary" "$operation/firmware.bin" || exit 1
	size=$(stat -c %s "$operation/firmware.bin") || exit 1
	run_command 0 "${command[@]}" -s "$address" -D "$operation/firmware.bin" || exit $?
	run_command 0 "${command[@]}" -s "$address:$size" -U "$readback" || exit $?
	cmp -- "$operation/firmware.bin" "$readback" || {
		printf 'Проверка Flash не пройдена; плата оставлена в DFU.\n' >&2; exit 1;
	}
	printf 'DFU: проверено %s байт, Flash совпадает с прошивкой.\n' "$size"
	printf 'Serial: %s\nAddress: %s\nSource: %s\n' "$DFU_SERIAL" "$address" "$FIRMWARE" > "$operation/VERIFIED"
	sha256sum -- "$operation/firmware.bin" "$readback" || exit 1
	# DfuSe command mode: leave only after readback verification, without rewriting.
	run_command 0 dfu_leave "${command[@]}" -s "$address:leave"
)

upload_firmware() {
	local answer path method=$UPLOAD_METHOD devices
	local -a command=("$PYTHON" -u "$ROOT/Tools/px4_uploader.py") ports=(auto 'Автоматически найти PX4 по USB')
	[[ -n $FIRMWARE && ( $FIRMWARE == *.px4 || $FIRMWARE == *.bin ) ]] || {
		printf 'Для загрузки нужен файл .px4 или .bin; укажите --firmware FILE.\n' >&2; return 1;
	}
	if (( ! DRY_RUN )); then
		[[ -s $FIRMWARE && -r $FIRMWARE ]] || { printf 'Не удаётся прочитать: %s\n' "$FIRMWARE" >&2; return 1; }
		FIRMWARE="$(cd -- "$(dirname -- "$FIRMWARE")" && pwd)/${FIRMWARE##*/}"
		if [[ -f ${FIRMWARE%/*}/SHA256SUMS ]]; then
			(cd -- "${FIRMWARE%/*}" && sha256sum --check SHA256SUMS) || return 1
		fi
		sha256sum -- "$FIRMWARE" || return 1
	fi
	if [[ $method != serial && -z $PORT ]]; then
		devices=$(dfu_devices) || return 1
		if [[ -n $DFU_SERIAL || -n $devices || $method == dfu ]]; then
			method=dfu
			if [[ -z $DFU_SERIAL ]]; then
				if [[ $devices == *$'\n'* ]]; then
					printf 'Подключено несколько DFU-плат. Укажите --dfu-serial:\n%s\n' "$devices" >&2; return 1
				fi
				DFU_SERIAL=$devices
			fi
			if (( ! DRY_RUN )) && [[ -z $DFU_SERIAL || $'\n'$devices$'\n' != *$'\n'"$DFU_SERIAL"$'\n'* ]]; then
				printf 'STM32 DFU не найдена. Подключите плату с зажатой BOOT и проверьте dfu-util -l.\n' >&2; return 1
			fi
		fi
	fi
	if [[ $method == dfu ]]; then upload_dfu; return $?; fi
	if [[ $FIRMWARE != *.px4 || ${FIRMWARE##*/} == *bootloader* ]]; then
		printf 'Этот файл требует STM32 DFU и одноимённый .elf. Подключите плату в DFU; используйте --method dfu.\n' >&2; return 1
	fi
	if (( ! DRY_RUN )) && { [[ -z $PYTHON ]] || ! "$PYTHON" -c 'import serial' 2>/dev/null; }; then
		printf 'Нужны Python и pyserial; используйте «Подготовить Python».\n' >&2; return 1
	fi
	if ((INTERACTIVE)); then
		for path in /dev/serial/by-id/* /dev/ttyACM* /dev/ttyUSB*; do
			[[ -e $path ]] && ports+=("$path" 'Последовательный порт')
		done
		ports+=(custom 'Ввести порт вручную')
		answer=$(choose 'Порт загрузчика' "$FIRMWARE" "${ports[@]}") || return 0
		case $answer in
			auto) PORT='' ;;
			custom) PORT=$(input_path 'Порт' 'Например /dev/ttyACM0') || return 0 ;;
			*) PORT=$answer ;;
		esac
		answer=$(choose 'Загрузить прошивку?' "$FIRMWARE
Порт: ${PORT:-авто}. Подключите плату с PX4 bootloader. Закройте подключение QGC к ней." no 'Назад' yes 'Загрузить этот файл на плату') || return 0
		[[ $answer == yes ]] || return 0
	fi
	[[ -n $PORT ]] && command+=(--port "$PORT")
	command+=(-- "$FIRMWARE")
	printf 'Загрузка: %s\nПорт: %s\n' "$FIRMWARE" "${PORT:-автоматический поиск}"
	run_command 1 "${command[@]}"
}

perform() {
	local goal='' terminal=0 backup config lock_fd status python_arg RESULT_DIR answer
	local -a PRODUCTS=()
	properties
	case $ACTION in
		info) info; return ;;
		doctor) doctor; return ;;
		list-configs) list_profiles; return ;;
		autotune-config)
			autotune_available || { printf 'В этой версии или цели нет выбора экспериментального автотюна.\n' >&2; return 1; } ;;
		build) ;;
		build-upload)
			[[ -n $NUTTX_DEFCONFIG ]] || {
				printf 'Сборка с загрузкой требует аппаратную цель.\n' >&2; return 1;
			} ;;
		px4-config)
			[[ $VARIANT != allyes* ]] || { printf 'allyes не поддерживает boardconfig.\n' >&2; return 1; }
			goal=boardconfig; terminal=1; config=${CONFIGS[$TARGET]} ;;
		kernel-config)
			if [[ -z $NUTTX_DEFCONFIG ]] || ! source_has "${NUTTX_DEFCONFIG#"$ROOT/"}"; then
				printf 'Для %s конфигуратора NuttX нет (SITL использует ОС компьютера).\n' "$TARGET" >&2; return 1
			fi
			goal=menuconfig; terminal=1; config=$NUTTX_DEFCONFIG ;;
		clean)
			[[ -d $BUILD_DIR ]] || { printf 'Каталог сборки пока не создан.\n'; return 0; }
			goal=clean ;;
		run)
			[[ $TARGET == px4_sitl_* ]] || { printf 'Запуск симулятора доступен только для px4_sitl.\n' >&2; return 1; }
			[[ $SIMULATOR =~ ^[a-zA-Z0-9_-]+$ ]] || return 1
			case $SIMULATOR in
				jmavsim) ;;
				gz_*|sihsim_*)
					if [[ $SOURCE_REF == current || -e $ROOT/.git ]]; then
						compgen -G "$ROOT/ROMFS/px4fmu_common/init.d-posix/airframes/[0-9]*_$SIMULATOR" >/dev/null
					else
						git -C "$LOCAL_ROOT" ls-tree -r --name-only "$SOURCE_COMMIT" -- ROMFS/px4fmu_common/init.d-posix/airframes | grep -E "/[0-9]+_$SIMULATOR$" >/dev/null
					fi || {
						printf 'Неизвестная модель: %s\n' "$SIMULATOR" >&2; return 1;
					} ;;
				*) printf 'Неизвестный симулятор: %s\n' "$SIMULATOR" >&2; return 1 ;;
			esac
			goal=$SIMULATOR; terminal=1 ;;
	esac
	if (( ! DRY_RUN )); then
		command -v flock >/dev/null || { printf 'Требуется flock (util-linux).\n' >&2; return 1; }
		mkdir -p "$STATE" || return 1
		exec {lock_fd}> "$STATE/lock" || return 1
		if ! flock -n "$lock_fd"; then
			printf 'Другая операция build.sh уже работает в этом репозитории.\n' >&2
			exec {lock_fd}>&-; return 1
		fi
	fi
	# A subshell releases the operation context on failures and cancellation.
	(
		if [[ $ACTION == upload ]]; then upload_firmware; exit $?; fi
		prepare_source || exit $?
		if [[ $ACTION == autotune-config ]]; then configure_autotune; exit $?; fi
		if [[ $ACTION == save-config || $ACTION == load-config ]]; then config_profile; exit $?; fi
		if [[ $ACTION == python-setup ]]; then
			command -v python3 >/dev/null || { printf 'Установите Python 3 и python3-venv.\n' >&2; exit 1; }
			if [[ ! -x $ROOT/.venv/bin/python ]]; then
				run_command 0 python3 -m venv "$ROOT/.venv" || exit $?
			fi
			run_command 0 "$ROOT/.venv/bin/python" -m pip install -r "$ROOT/Tools/setup/requirements.txt"
			exit $?
		fi
		if (( ! DRY_RUN )); then
			doctor || exit 1
			if ((terminal)) && [[ $ACTION != run && ! -t 0 ]]; then
				printf 'Конфигуратор нужно запускать в интерактивном терминале.\n' >&2; exit 1
			fi
			if [[ -n ${config:-} ]]; then
				mkdir -p "$STATE/backups" || exit 1
				backup=$(mktemp -d "$STATE/backups/${TARGET}-$(date +%Y%m%d-%H%M%S)-XXXXXX") || exit 1
				cp -p -- "$config" "$backup/${config##*/}" || exit 1
				printf 'Конфигуратор сохраняет: %s\nРезервная копия: %s\n' "$config" "$backup"
			fi
			if [[ $ACTION == build || $ACTION == build-upload ]]; then prepare_artifacts || exit 1; fi
		fi
		local -a command=(make --no-print-directory -C "$ROOT" "$TARGET")
		[[ -n $goal ]] && command+=("$goal")
		command+=("PX4_MAKE_ARGS=-j$JOBS")
		if [[ -n $PYTHON ]]; then
			printf -v python_arg '%q' "$PYTHON"
			export PATH="${PYTHON%/*}:$PATH"
			export CMAKE_ARGS="${CMAKE_ARGS:-} -DPython3_EXECUTABLE=$python_arg -DPYTHON_EXECUTABLE=$python_arg"
		fi
		export PX4_CMAKE_BUILD_TYPE=$BUILD_TYPE CMAKE_BUILD_PARALLEL_LEVEL=$JOBS
		printf '\nИсходники: %s\nЦель: %s | %s | задач: %s\nPython: %s\n' "$SOURCE_REF" "$TARGET" "$BUILD_TYPE" "$JOBS" "${PYTHON:-не найден}"
		[[ $SOURCE_REF != current ]] || printf '%s\n' "$(current_source_label)"
		if autotune_available; then printf 'Автотюн: %s\n' "$(autotune_mode)"; fi
		run_command "$terminal" "${command[@]}" || exit $?
		if [[ $ACTION == build || $ACTION == build-upload ]]; then
			if ((DRY_RUN)); then
				if [[ $ACTION == build-upload ]]; then
					FIRMWARE="$STATE/artifacts/$TARGET/<текущая-сборка>/$TARGET.px4"
					upload_firmware || exit $?
				fi
			else
				collect_artifacts || exit 1
				FIRMWARE="$RESULT_DIR/$TARGET.px4"
				[[ -s $FIRMWARE ]] || FIRMWARE="$RESULT_DIR/$TARGET.bin"
				if [[ $ACTION == build-upload ]]; then
					upload_firmware || exit $?
				elif ((INTERACTIVE)) && [[ -s $FIRMWARE ]]; then
					answer=$(choose 'Сборка завершена' "$FIRMWARE" 'done' 'Вернуться в меню' upload 'Загрузить прошивку сейчас') || exit 0
					if [[ $answer == upload ]]; then upload_firmware || exit $?; fi
				fi
			fi
		fi
		exit 0
	)
	status=$?
	if [[ -n ${lock_fd:-} ]]; then flock -u "$lock_fd"; exec {lock_fd}>&-; fi
	return "$status"
}

if ((LIST_REFS)); then list_refs; exit $?; fi
set_source "$SOURCE_REF" || die "Не удалось выбрать версию: $SOURCE_REF"
if ((LIST_TARGETS)); then printf '%s\n' "${!CONFIGS[@]}" | LC_ALL=C sort; exit 0; fi
[[ -n ${CONFIGS[$TARGET]:-} ]] || die "Неизвестная конфигурация для $SOURCE_REF: $TARGET"
properties
if [[ -n $ACTION ]]; then perform; exit $?; fi
[[ -t 0 && -t 1 ]] || die 'Для меню нужен терминал; используйте --action или --help'
INTERACTIVE=1
WIDTH=$(tput cols 2>/dev/null || printf '80'); HEIGHT=$(tput lines 2>/dev/null || printf '24')
((WIDTH > 110)) && WIDTH=110
((HEIGHT > 30)) && HEIGHT=30
if (( ! FORCE_TEXT && WIDTH >= 70 && HEIGHT >= 22 )) && command -v whiptail >/dev/null; then UI=whiptail; fi

while :; do
	items=(source 'Выбрать текущую ветку, другую ветку или версию' target 'Выбрать плату и вариант' build 'Собрать прошивку' px4-config 'Настроить PX4: модули и драйверы')
	if autotune_available; then items+=(autotune-config "Автотюн: $(autotune_mode) — выбрать реализацию"); fi
	[[ -n $NUTTX_DEFCONFIG ]] && items+=(kernel-config 'Настроить ядро NuttX')
	items+=(save-config 'Сохранить кастомную конфигурацию' load-config 'Загрузить кастомную конфигурацию')
	[[ -n $NUTTX_DEFCONFIG ]] && items+=(build-upload 'Собрать и загрузить прошивку')
	items+=(upload 'Загрузить имеющийся .px4 / .bin без сборки')
	[[ $TARGET == px4_sitl_* ]] && items+=(run 'Собрать и запустить SITL')
	items+=(options 'Тип сборки и параллельные задачи' info 'Пути и результаты сборки' doctor 'Проверить зависимости'
		python-setup 'Подготовить Python: .venv и requirements.txt' clean 'Очистить результаты выбранной сборки')
	ACTION=$(choose 'PX4 Build' "$(current_source_label)
Выбрано: $SOURCE_REF
$TARGET | $BUILD_TYPE | задач: $JOBS" "${items[@]}") || break
	case $ACTION in
		source) select_source || :; continue ;;
		target) select_target || :; continue ;;
		autotune-config)
			AUTOTUNE=$(choose 'Автотюн' "Плата: $TARGET. Текущий режим: $(autotune_mode).
Выбор сохраняется в конфигурации платы; затем выполните сборку." \
				standard 'Стандартный MC Autotune' \
				experimental 'Экспериментальный: проверка отклика, дополнительная Flash и ~35 КиБ heap' \
				disabled 'Отключить MC Autotune') || continue ;;
		save-config|load-config) select_profile || { pause; continue; } ;;
		upload) select_firmware || continue ;;
		options)
			next_type=$(choose 'Тип сборки' 'Профиль компиляции' RelWithDebInfo 'Оптимизация + символы отладки' Release 'Оптимизация' Debug 'Отладка' MinSizeRel 'Минимальный размер') || continue
			next_jobs=$(choose 'Параллельные задачи' 'Меньше задач — меньше потребление памяти' 1 'Последовательно' 2 '2 задачи' 4 '4 задачи' 8 '8 задач' 16 '16 задач') || continue
			BUILD_TYPE=$next_type; JOBS=$next_jobs
			continue ;;
		run)
			SIMULATOR=$(choose 'Симулятор' 'Ctrl+C останавливает запуск' gz_x500 'Gazebo: x500' jmavsim 'jMAVSim' sihsim_quadx 'SIH: без отдельного симулятора') || continue ;;
		clean)
			choose 'Очистка' "Удалить скомпилированные результаты $TARGET?" yes 'Выполнить make clean для этой цели' >/dev/null || continue ;;
	esac
	perform || :
	pause
done
