# Запустить контейнер

cd /home/kirillz/src/PX4-Autopilot/
docker run -it --rm \
  --env=LOCAL_USER_ID="$(id -u)" \
  -v ~/src/PX4-Autopilot:/src/PX4-Autopilot:rw \
  --workdir /src/PX4-Autopilot \
  --name px4-kakuteh7-build \
  px4io/px4-dev:v1.16.1 \
  bash


## Если сменили путь при сборке, очистить старый кэш и хвосты
cd /src/PX4-Autopilot
rm -rf build/holybro_kakuteh7_default
make holybro_kakuteh7_default


