#!/usr/bin/env bash
# ============================================================
#  pin-npm-cert.sh - пришпилить сертификат NPM в MeshCentral
#
#  Зачем: при TLS-offload агенты видят сертификат NPM, а
#  MeshCentral по умолчанию ждёт свой собственный. В логах:
#    "Agent bad web cert hash ... holding connection"
#  Скрипт считает SHA384 сертификата, которым NPM обслуживает
#  домен, и прописывает его в domains[""].certhash конфига
#  MeshCentral. Проверка хэша при этом НЕ отключается.
#
#  Когда запускать:
#    - после первичной настройки TLS-offload;
#    - после продления/замены сертификата в NPM.
#
#  Использование:
#    ./scripts/pin-npm-cert.sh              # прописать и перезапустить MC
#    ./scripts/pin-npm-cert.sh --check      # только показать хэши
#    ./scripts/pin-npm-cert.sh --no-restart # без перезапуска
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MC_CONTAINER="${MC_CONTAINER:-meshcentral}"
NPM_CONTAINER="${NPM_CONTAINER:-nginx-proxy}"
DOMAIN="${DOMAIN:-rmm.zr.local}"
MC_CONFIG="${MC_CONFIG:-/opt/meshcentral/meshcentral-data/config.json}"
NPM_PROXY_DIR="${NPM_PROXY_DIR:-/data/nginx/proxy_host}"

CHECK_ONLY=0
NO_RESTART=0

usage() {
    cat <<'EOF'
pin-npm-cert.sh - пришпилить SHA384 сертификата NPM в MeshCentral

  --check        только показать текущий и вычисленный хэши
  --no-restart   обновить конфиг без перезапуска MeshCentral
  --help         эта справка

Переменные окружения:
  DOMAIN         домен для поиска proxy_host (по умолчанию rmm.zr.local)
  MC_CONTAINER   контейнер MeshCentral (meshcentral)
  NPM_CONTAINER  контейнер NPM (nginx-proxy)
EOF
    exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check)       CHECK_ONLY=1; shift ;;
        --no-restart)  NO_RESTART=1; shift ;;
        --help|-h)     usage 0 ;;
        *) echo "Неизвестный аргумент: $1" >&2; usage 1 ;;
    esac
done

die() { echo "ОШИБКА: $*" >&2; exit 1; }

docker inspect -f '{{.State.Running}}' "$MC_CONTAINER" 2>/dev/null | grep -q true \
    || die "контейнер $MC_CONTAINER не запущен"
docker inspect -f '{{.State.Running}}' "$NPM_CONTAINER" 2>/dev/null | grep -q true \
    || die "контейнер $NPM_CONTAINER не запущен"

# --- 1. Находим конфиг NPM для домена ---
CONF="$(docker exec "$NPM_CONTAINER" sh -c \
    "grep -l 'server_name $DOMAIN;' $NPM_PROXY_DIR/*.conf 2>/dev/null | head -1" || true)"
[[ -n "$CONF" ]] || die "в NPM не найден proxy_host для $DOMAIN"

CERT="$(docker exec "$NPM_CONTAINER" sh -c \
    "grep -m1 'ssl_certificate ' '$CONF' | sed -E 's/.*ssl_certificate[[:space:]]+([^;]+);.*/\1/'" || true)"
[[ -n "$CERT" ]] || die "в $CONF не найден ssl_certificate"

# --- 2. Считаем SHA384 сертификата (тот же, что присылает агент) ---
HASH="$(docker exec "$NPM_CONTAINER" sh -c \
    "openssl x509 -in '$CERT' -outform DER | openssl dgst -sha384 -r" | cut -d' ' -f1)"
[[ "$HASH" =~ ^[0-9a-f]{96}$ ]] || die "не удалось вычислить SHA384 (получено: '$HASH')"

echo "Домен   : $DOMAIN"
echo "NPM conf: $CONF"
echo "Сертифик: $CERT"
echo "SHA384  : $HASH"

# --- 3. Читаем текущее значение ---
CURRENT="$(docker exec "$MC_CONTAINER" node -e '
const fs = require("fs");
const cfg = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
const d = (cfg.domains || {})[""] || {};
process.stdout.write(d.certhash || "");
' "$MC_CONFIG" 2>/dev/null || true)"

echo "Текущий : ${CURRENT:-(не задан)}"

if [[ "$CURRENT" == "$HASH" ]]; then
    echo "✓ Хэш уже актуален, ничего менять не нужно."
    exit 0
fi

if [[ "$CHECK_ONLY" == "1" ]]; then
    echo "→ Требуется обновление (запусти без --check)."
    exit 1
fi

# --- 4. Бэкап и запись ---
TS="$(date +%Y%m%d-%H%M%S)"
docker exec "$MC_CONTAINER" sh -c \
    "cp '$MC_CONFIG' '$MC_CONFIG.bak-$TS'" \
    || die "не удалось создать бэкап конфига"
echo "Бэкап   : $MC_CONFIG.bak-$TS"

docker exec "$MC_CONTAINER" node -e '
const fs = require("fs");
const [p, h] = process.argv.slice(1);
const cfg = JSON.parse(fs.readFileSync(p, "utf8"));
cfg.domains = cfg.domains || {};
cfg.domains[""] = cfg.domains[""] || {};
cfg.domains[""].certhash = h;
fs.writeFileSync(p, JSON.stringify(cfg, null, 2) + "\n");
' "$MC_CONFIG" "$HASH" || die "не удалось обновить $MC_CONFIG"

echo "✓ Записан новый certhash."

if [[ "$NO_RESTART" == "1" ]]; then
    echo "Перезапуск пропущен (--no-restart). Не забудь перезапустить $MC_CONTAINER."
    exit 0
fi

# --- 5. Перезапуск и ожидание healthcheck ---
echo "Перезапускаю $MC_CONTAINER..."
docker restart "$MC_CONTAINER" >/dev/null

for _ in $(seq 1 30); do
    STATUS="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$MC_CONTAINER" 2>/dev/null || echo unknown)"
    if [[ "$STATUS" == "healthy" || "$STATUS" == "running" ]]; then
        echo "✓ $MC_CONTAINER: $STATUS"
        exit 0
    fi
    sleep 2
done

die "$MC_CONTAINER не стал healthy за 60 секунд — проверь docker logs"
