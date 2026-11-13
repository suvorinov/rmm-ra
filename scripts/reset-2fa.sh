#!/usr/bin/env bash
# ============================================================
#  reset-2fa.sh - аварийный сброс 2FA у пользователя MeshCentral
#
#  Зачем: если единственный администратор потерял телефон и
#  резервные коды, войти в панель невозможно. Скрипт снимает
#  с учётной записи все вторые факторы, и вход снова работает
#  по одному паролю.
#
#  Снимает поля: otpsecret (TOTP), otpkeys (резервные коды),
#  otphkeys (ключи безопасности), otpekey (2FA по почте),
#  otpduo (Duo), otpdev (push-устройство).
#
#  Использование:
#    ./scripts/reset-2fa.sh                     # только показать состояние
#    ./scripts/reset-2fa.sh --apply             # сбросить (нужен --yes)
#    ./scripts/reset-2fa.sh --apply --yes       # без подтверждения
#    ./scripts/reset-2fa.sh --apply --yes --user user//admin
#
#  ВНИМАНИЕ: скрипт останавливает MeshCentral на время правки БД.
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MC_CONTAINER="${MC_CONTAINER:-meshcentral}"
MC_DATA="${MC_DATA:-/opt/meshcentral/meshcentral-data}"
MC_DB="${MC_DB:-$MC_DATA/meshcentral.db}"
TARGET_USER="${TARGET_USER:-}"

APPLY=0
ASSUME_YES=0

usage() {
    cat <<'EOF'
reset-2fa.sh - аварийный сброс 2FA в MeshCentral

  --apply        снять вторые факторы с учётной записи
  --yes          не спрашивать подтверждение (для --apply)
  --user <id>    ID пользователя, по умолчанию user//suvorinov
  --help         эта справка

Переменные окружения: MC_CONTAINER, MC_DB, TARGET_USER
EOF
    exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --apply) APPLY=1; shift ;;
        --yes|-y) ASSUME_YES=1; shift ;;
        --user) TARGET_USER="${2:?--user требует значение}"; shift 2 ;;
        --help|-h) usage 0 ;;
        *) echo "Неизвестный аргумент: $1" >&2; usage 1 ;;
    esac
done

TARGET_USER="${TARGET_USER:-user//suvorinov}"

die() { echo "ОШИБКА: $*" >&2; exit 1; }

docker inspect -f '{{.State.Running}}' "$MC_CONTAINER" 2>/dev/null | grep -q true \
    || die "контейнер $MC_CONTAINER не запущен"

# Показываем состояние (читаем файл БД напрямую, без записи)
show_state() {
    docker exec "$MC_CONTAINER" node -e '
const fs = require("fs");
const [dbPath, userId] = process.argv.slice(1);
let last = null;
for (const line of fs.readFileSync(dbPath, "utf8").split("\n")) {
    if (!line.trim()) continue;
    let o; try { o = JSON.parse(line); } catch (e) { continue; }
    if (o._id === userId) last = o;
}
if (!last) { console.log("  пользователь " + userId + " не найден в " + dbPath); process.exit(2); }
const f = {
    "TOTP (приложение)": last.otpsecret,
    "резервные коды": (last.otpkeys && last.otpkeys.keys) ? last.otpkeys.keys.length + " шт." : null,
    "ключи безопасности": last.otphkeys ? "есть" : null,
    "2FA по почте": last.otpekey,
    "Duo": last.otpduo,
    "push-устройство": last.otpdev
};
let any = false;
for (const k of Object.keys(f)) {
    if (f[k]) { any = true; console.log("  " + k + ": " + (k.startsWith("TOTP") ? "включена" : (f[k] === true ? "включено" : f[k]))); }
    else console.log("  " + k + ": —");
}
console.log(any ? "  => 2FA включена" : "  => 2FA выключена");
' "$MC_DB" "$TARGET_USER"
}

echo "Контейнер : $MC_CONTAINER"
echo "База      : $MC_DB"
echo "Пользователь: $TARGET_USER"
echo "Текущее состояние:"
show_state

if [[ "$APPLY" != "1" ]]; then
    echo
    echo "Это режим только-чтение. Чтобы сбросить: $0 --apply --yes --user $TARGET_USER"
    exit 0
fi

if [[ "$ASSUME_YES" != "1" ]]; then
    echo
    echo "Будут удалены ВСЕ вторые факторы у $TARGET_USER."
    read -r -p "Продолжить? Введите yes: " ans
    [[ "$ans" == "yes" ]] || { echo "Отменено."; exit 1; }
fi

TS="$(date +%Y%m%d-%H%M%S)"

# Хостовый путь к каталогу данных (файлы принадлежат root, поэтому
# работаем через вспомогательные контейнеры от root)
DATA_HOST="$(docker inspect -f \
    '{{range .Mounts}}{{if eq .Destination "/opt/meshcentral/meshcentral-data"}}{{.Source}}{{end}}{{end}}' \
    "$MC_CONTAINER")"
[[ -n "$DATA_HOST" ]] || die "не удалось определить каталог meshcentral-data"
IMAGE="$(docker inspect -f '{{.Config.Image}}' "$MC_CONTAINER")"
BACKUP="$MC_DB.bak-$TS"

# --- 1. Останавливаем MeshCentral, чтобы не было конкурентной записи ---
echo "Останавливаю $MC_CONTAINER..."
docker stop "$MC_CONTAINER" >/dev/null

# --- 2. Бэкап БД ---
docker run --rm -v "$DATA_HOST:/data" busybox \
    cp -a /data/meshcentral.db "/data/meshcentral.db.bak-$TS" \
    || die "не удалось сделать бэкап БД"
echo "Бэкап БД  : $BACKUP"

# --- 3. Правка БД через NeDB из образа MeshCentral ---
docker run --rm -v "$DATA_HOST:/data" --entrypoint node "$IMAGE" -e '
const NeDB = require("/opt/meshcentral/meshcentral/node_modules/@seald-io/nedb");
const [dbPath, userId] = process.argv.slice(1);
const db = new NeDB({ filename: dbPath });
db.loadDatabase((err) => {
    if (err) { console.error("load error:", err); process.exit(1); }
    db.update({ _id: userId },
        { $unset: { otpsecret: true, otpkeys: true, otphkeys: true, otpekey: true, otpduo: true, otpdev: true } },
        {}, (e, n) => {
            if (e) { console.error("update error:", e); process.exit(1); }
            console.log("обновлено записей:", n);
            db.compactDatafile((err2) => {
                if (err2) { console.error("compact error:", err2); process.exit(1); }
                console.log("БД уплотнена");
                process.exit(0);
            });
        });
});
' /data/meshcentral.db "$TARGET_USER" || die "не удалось обновить БД (MeshCentral остановлен, БД в бэкапе: $BACKUP)"

# --- 4. Запускаем обратно ---
echo "Запускаю $MC_CONTAINER..."
docker start "$MC_CONTAINER" >/dev/null

for _ in $(seq 1 30); do
    STATUS="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$MC_CONTAINER" 2>/dev/null || echo unknown)"
    if [[ "$STATUS" == "healthy" || "$STATUS" == "running" ]]; then
        echo "✓ $MC_CONTAINER: $STATUS"
        echo "Новое состояние:"
        show_state
        echo
        echo "Вход снова возможен по одному паролю. Сразу включи 2FA заново и сохрани резервные коды."
        exit 0
    fi
    sleep 2
done

die "$MC_CONTAINER не стал healthy за 60 секунд — проверь docker logs. Бэкап: $BACKUP"
