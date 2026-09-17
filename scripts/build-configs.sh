#!/usr/bin/env bash
# Source/version and configuration profile helpers for build.sh.

autotune_available() {
	case $VARIANT in *bootloader*|allyes*) return 1 ;; esac
	local path=src/modules/mc_autotune_attitude_control/Kconfig
	source_has "$path" && source_read "$path" | grep -q '^config MC_AUTOTUNE_EXPERIMENTAL$'
}

autotune_mode() {
	local config line enabled=n experimental=n
	local -a configs=("${CONFIGS[$TARGET]}")
	case $VARIANT in
		*default*|*performance-test*|*bootloader*) ;;
		*) configs=("$BOARD_DIR/default.px4board" "${configs[@]}") ;;
	esac
	for config in "${configs[@]}"; do
		source_has "${config#"$ROOT/"}" || continue
		while IFS= read -r line || [[ -n $line ]]; do
			case $line in
				CONFIG_MODULES_MC_AUTOTUNE_ATTITUDE_CONTROL=*) enabled=${line#*=} ;;
				'# CONFIG_MODULES_MC_AUTOTUNE_ATTITUDE_CONTROL is not set') enabled=n ;;
				CONFIG_MC_AUTOTUNE_EXPERIMENTAL=*) experimental=${line#*=} ;;
				'# CONFIG_MC_AUTOTUNE_EXPERIMENTAL is not set') experimental=n ;;
			esac
		done < <(source_read "${config#"$ROOT/"}")
	done
	if [[ $enabled != y ]]; then printf 'disabled'
	elif [[ $experimental == y ]]; then printf 'experimental'
	else printf 'standard'; fi
}

# Called under the same repository lock as builds and configuration profiles.
configure_autotune() {
	local config=${CONFIGS[$TARGET]} backup temporary enabled=y experimental=n
	[[ $AUTOTUNE != disabled ]] || enabled=n
	[[ $AUTOTUNE != experimental ]] || experimental=y
	printf 'Автотюн: %s -> %s\nКонфигурация: %s\n' "$(autotune_mode)" "$AUTOTUNE" "$config"
	((DRY_RUN)) && return 0
	mkdir -p "$STATE/backups" || return 1
	backup=$(mktemp -d "$STATE/backups/${TARGET}-autotune-$(date +%Y%m%d-%H%M%S)-XXXXXX") || return 1
	cp -p -- "$config" "$backup/${config##*/}" || return 1
	temporary=$(mktemp "$config.XXXXXX") || return 1
	if ! awk -v enabled="$enabled" -v experimental="$experimental" '
		/^(# )?CONFIG_(MODULES_MC_AUTOTUNE_ATTITUDE_CONTROL|MC_AUTOTUNE_EXPERIMENTAL)(=| is not set)/ { next }
		{ print }
		END {
			print "CONFIG_MODULES_MC_AUTOTUNE_ATTITUDE_CONTROL=" enabled
			print "CONFIG_MC_AUTOTUNE_EXPERIMENTAL=" experimental
		}' "$config" > "$temporary" || ! chmod --reference="$config" "$temporary" || ! mv -- "$temporary" "$config"; then
		rm -f -- "$temporary"
		return 1
	fi
	printf 'Сохранено. Резервная копия: %s\nДля применения соберите прошивку.\n' "$backup"
}

current_source_label() {
	local branch sha dirty
	branch=$(git -C "$LOCAL_ROOT" symbolic-ref --short -q HEAD) || branch='detached HEAD'
	sha=$(git -C "$LOCAL_ROOT" rev-parse --short HEAD) || return 1
	dirty=$(git -C "$LOCAL_ROOT" status --porcelain --untracked-files=normal) || return 1
	printf 'Текущая ветка: %s (%s); локальные коммиты + рабочие файлы%s' \
		"$branch" "$sha" "${dirty:+; есть незакоммиченные изменения}"
}

list_refs() {
	printf 'current\t%s\n' "$(current_source_label)"
	git -C "$LOCAL_ROOT" for-each-ref --sort=refname \
		--format='%(refname)%09%(objectname:short) %(subject)' refs/heads refs/remotes refs/tags
}

# Read the selected revision without creating a worktree (info and dry-run).
source_has() {
	if [[ $SOURCE_REF == current || -e $ROOT/.git ]]; then
		[[ -e $ROOT/$1 ]]
	else
		git -C "$LOCAL_ROOT" cat-file -e "$SOURCE_COMMIT:$1" 2>/dev/null
	fi
}

source_read() {
	if [[ $SOURCE_REF == current || -e $ROOT/.git ]]; then
		cat -- "$ROOT/$1"
	else
		git -C "$LOCAL_ROOT" show "$SOURCE_COMMIT:$1"
	fi
}

set_source() {
	local ref=$1 commit='' path name next_root=$LOCAL_ROOT
	local -A next_configs=()
	if [[ $ref != current ]]; then
		commit=$(git -C "$LOCAL_ROOT" rev-parse --verify --end-of-options "$ref^{commit}") || return 1
		next_root="$STATE/sources/$commit"
	fi
	while IFS= read -r path; do
		[[ $path =~ ^boards/[^/]+/[^/]+/[^/]+\.px4board$ ]] || continue
		name=${path#boards/}; name=${name%.px4board}
		next_configs[${name//\//_}]="$next_root/$path"
	done < <(
		if [[ $ref == current || -e $next_root/.git ]]; then
			for path in "$next_root"/boards/*/*/*.px4board; do
				[[ -f $path ]] && printf '%s\n' "${path#"$next_root/"}"
			done
		else
			git -C "$LOCAL_ROOT" ls-tree -r --name-only "$commit" -- boards
		fi
	)
	((${#next_configs[@]})) || { printf 'Версия не содержит конфигураций PX4: %s\n' "$ref" >&2; return 1; }
	SOURCE_REF=$ref SOURCE_COMMIT=$commit ROOT=$next_root
	CONFIGS=()
	for name in "${!next_configs[@]}"; do CONFIGS["$name"]=${next_configs[$name]}; done
}

select_source() {
	local mode selected ref
	local -a entries=()
	mode=$(choose 'Версия исходников' "$(current_source_label)" \
		current 'Собирать текущую ветку со всеми локальными изменениями' \
		branch 'Локальная ветка' remote 'Удалённая ветка' tag 'Версия / тег' \
		manual 'Ввести ветку, тег или SHA') || return
	case $mode in
		current) selected=current ;;
		manual) selected=$(input_path 'Версия' 'Ветка, тег или SHA') || return ;;
		*)
			case $mode in branch) ref=refs/heads ;; remote) ref=refs/remotes ;; tag) ref=refs/tags ;; esac
			while IFS= read -r ref; do
				[[ $ref == */HEAD ]] && continue
				entries+=("$ref" "${ref#refs/*/}")
			done < <(git -C "$LOCAL_ROOT" for-each-ref --sort=-version:refname --format='%(refname)' "$ref")
			((${#entries[@]})) || { printf 'В этой категории нет веток / тегов.\n'; return 1; }
			selected=$(choose 'Версия' 'Выберите известную локальному Git версию' "${entries[@]}") || return ;;
	esac
	[[ -n $selected ]] || return 1
	set_source "$selected" || return
	if [[ -z ${CONFIGS[$TARGET]:-} ]]; then
		TARGET=px4_sitl_default
		if [[ -z ${CONFIGS[$TARGET]:-} ]]; then
			TARGET=$(printf '%s\n' "${!CONFIGS[@]}" | LC_ALL=C sort | sed -n '1p')
		fi
		printf 'Для этой версии выбрана цель: %s\n' "$TARGET"
	fi
	properties
}

# Called only under build.sh's repository-wide lock. Keep the worktree so that
# configured files and incremental builds survive subsequent menu actions.
prepare_source() {
	[[ $SOURCE_REF == current ]] && return 0
	printf 'Исходники: %s (%s)\nКаталог: %s\n' "$SOURCE_REF" "$SOURCE_COMMIT" "$ROOT"
	if [[ -e $ROOT/.git ]]; then
		[[ $(git -C "$ROOT" rev-parse HEAD) == "$SOURCE_COMMIT" ]] || {
			printf 'HEAD рабочего каталога изменён: %s\n' "$ROOT" >&2; return 1;
		}
	else
		if (( ! DRY_RUN )); then mkdir -p "$STATE/sources" || return 1; fi
		run_command 0 git -C "$LOCAL_ROOT" worktree add --detach "$ROOT" "$SOURCE_COMMIT" || return
	fi
	case $ACTION in
		build|build-upload|px4-config|kernel-config|run)
			run_command 0 git -C "$ROOT" submodule update --init --recursive || return ;;
	esac
}

profile_files() {
	PROFILE_FILES=("$VARIANT.px4board")
	# Other labels are overlays on default.px4board; keep that base as well.
	case $VARIANT in
		*default*|*performance-test*|*bootloader*) ;;
		*) PROFILE_FILES+=(default.px4board) ;;
	esac
	[[ -z $NUTTX_DEFCONFIG ]] || PROFILE_FILES+=("${NUTTX_DEFCONFIG#"$BOARD_DIR/"}")
}

list_profiles() {
	local path
	printf 'Профили %s: %s\n' "$TARGET" "$PROFILE_DIR"
	for path in "$PROFILE_DIR"/*; do
		[[ -f $path/target ]] && printf '  %s\n' "${path##*/}"
	done
	return 0
}

select_profile() {
	local path
	local -a entries=()
	if [[ $ACTION == save-config ]]; then
		PROFILE=$(input_path 'Сохранить конфигурацию' "Имя нового профиля в $PROFILE_DIR") || return
	else
		for path in "$PROFILE_DIR"/*; do
			[[ -f $path/target ]] && entries+=("${path##*/}" "$path")
		done
		((${#entries[@]})) || { printf 'Нет сохранённых профилей: %s\n' "$PROFILE_DIR"; return 1; }
		PROFILE=$(choose 'Загрузить конфигурацию' "Плата: $TARGET. Текущие конфиги будут скопированы в backups." "${entries[@]}") || return
	fi
	[[ -n $PROFILE ]]
}

config_profile() {
	local destination="$PROFILE_DIR/$PROFILE" file restored_file saved_target backup staging
	local -a PROFILE_FILES=()
	[[ $PROFILE =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || {
		printf 'Имя профиля: латинские буквы, цифры, _, -, .; первый символ — буква или цифра.\n' >&2; return 1;
	}
	profile_files
	if [[ $ACTION == save-config ]]; then
		[[ ! -e $destination ]] || { printf 'Профиль уже существует: %s\n' "$destination" >&2; return 1; }
		for file in "${PROFILE_FILES[@]}"; do
			source_has "${BOARD_DIR#"$ROOT/"}/$file" || { printf 'Конфиг не найден: %s\n' "$file" >&2; return 1; }
		done
		printf 'Сохранить конфигурацию: %s\n' "$destination"
		((DRY_RUN)) && return 0
		mkdir -p "$PROFILE_DIR" || return 1
		staging=$(mktemp -d "$PROFILE_DIR/.saving-XXXXXX") || return 1
		for file in "${PROFILE_FILES[@]}"; do
			if ! mkdir -p "$staging/$(dirname -- "$file")" || ! cp -- "$BOARD_DIR/$file" "$staging/$file"; then
				rm -rf -- "$staging"; return 1
			fi
		done
		if ! { printf '%s\n' "$TARGET" > "$staging/target" &&
			printf 'Ref: %s\nCommit: %s\n' "$SOURCE_REF" "$(git -C "$ROOT" rev-parse HEAD)" > "$staging/source.txt" &&
			mv -- "$staging" "$destination"; }; then
			rm -rf -- "$staging"; return 1
		fi
	else
		IFS= read -r saved_target < "$destination/target" || return 1
		[[ $saved_target == "$TARGET" ]] || { printf 'Профиль создан для другой цели: %s\n' "$saved_target" >&2; return 1; }
		for file in "${PROFILE_FILES[@]}"; do
			if [[ ! -f $destination/$file || ! -r $destination/$file ]] || ! source_has "${BOARD_DIR#"$ROOT/"}/$file"; then
				printf 'Профиль несовместим или неполон: %s\n' "$file" >&2; return 1;
			fi
		done
		if [[ -z $NUTTX_DEFCONFIG && -d $destination/nuttx-config ]]; then
			printf 'Профиль NuttX несовместим с выбранной версией платы.\n' >&2; return 1
		fi
		printf 'Загрузить конфигурацию: %s\nВ каталог платы: %s\n' "$destination" "$BOARD_DIR"
		((DRY_RUN)) && return 0
		mkdir -p "$STATE/backups" || return 1
		backup=$(mktemp -d "$STATE/backups/${TARGET}-load-$(date +%Y%m%d-%H%M%S)-XXXXXX") || return 1
		for file in "${PROFILE_FILES[@]}"; do
			mkdir -p "$backup/$(dirname -- "$file")" && cp -p -- "$BOARD_DIR/$file" "$backup/$file" || return 1
		done
		printf 'Резервная копия: %s\n' "$backup"
		for file in "${PROFILE_FILES[@]}"; do
			# Do not preserve timestamps: CMake must see the changed inputs.
			if ! cp -- "$destination/$file" "$BOARD_DIR/$file"; then
				for restored_file in "${PROFILE_FILES[@]}"; do
					cp -- "$backup/$restored_file" "$BOARD_DIR/$restored_file" || return 1
				done
				return 1
			fi
		done
	fi
}
