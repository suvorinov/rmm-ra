#!/usr/bin/env bash
# ============================================================
#  npm-ws-timeout.sh - таймауты NPM для WebSocket-соединений
#
#  Зачем: у Nginx Proxy Manager дефолтный proxy_read_timeout
#  всего 90 секунд. Агенты MeshCentral держат WebSocket и могут
#  молчать дольше, поэтому NPM закрывает соединение, агент
#  переподключается и устройство "моргает" online/offline.
#  В логах NPM это выглядит как повторяющиеся GET /agent.ashx
#  (101 Switching Protocols) примерно раз в 90 секунд.
#
#  Скрипт добавляет в /data/nginx/custom/server_proxy.conf
#  (этот файл NPM подключает в каждый server{} проксируемых
#  хостов) proxy_read_timeout и proxy_send_timeout по 3600s.
#  Значения, которые уже заданы в файле, не перезаписываются.
#
#  Использование:
#    ./scripts/npm-ws-timeout.sh           # добавить и перезагрузить NPM
#    ./scripts/npm-ws-timeout.sh --check   # только показать состояние
#
#  Переменные окружения:
#    NPM_CONTAINER  контейнер NPM (по умолчанию nginx-proxy)
# ============================================================

set -euo pipefail

NPM_CONTAINER="${NPM_CONTAINER:-nginx-proxy}"
CUSTOM_FILE="/data/nginx/custom/server_proxy.conf"

CHECK_ONLY=0

usage() {
    sed -n '2,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check)    CHECK_ONLY=1; shift ;;
        -h|--help)  usage 0 ;;
        *) echo "Неизвестный аргумент: $1" >&2; usage 1 ;;
    esac
done

die() { echo "ОШИБКА: $*" >&2; exit 1; }

docker inspect -f '{{.State.Running}}' "$NPM_CONTAINER" 2>/dev/null | grep -q true \
    || die "контейнер $NPM_CONTAINER не запущен"

read_timeout() {
    docker exec "$NPM_CONTAINER" sh -c \
        "grep -E '^[[:space:]]*proxy_read_timeout[[:space:]]' '$CUSTOM_FILE' 2>/dev/null | tail -1" \
        | sed -E 's/.*proxy_read_timeout[[:space:]]+([^;]+);.*/\1/' || true
}

CURRENT="$(read_timeout)"

echo "Контейнер: $NPM_CONTAINER"
echo "Файл     : $CUSTOM_FILE"
echo "Таймаут  : ${CURRENT:-(не задан, действует дефолт NPM 90s)}"

if [[ -n "$CURRENT" ]]; then
    echo "✓ В custom-конфиге уже задан proxy_read_timeout ($CURRENT)."
    if [[ "$CHECK_ONLY" == "0" ]]; then
        echo "  Менять ничего не буду. Если нужно другое значение — поправь файл вручную."
    fi
    exit 0
fi

if [[ "$CHECK_ONLY" == "1" ]]; then
    echo "→ Требуется добавить таймауты (запусти без --check)."
    exit 1
fi

echo "Добавляю таймауты WebSocket в $CUSTOM_FILE..."
printf '%s\n' \
    '' \
    '# WebSocket: агенты MeshCentral могут молчать дольше 90s (дефолт NPM).' \
    '# Добавлено scripts/npm-ws-timeout.sh.' \
    'proxy_read_timeout 3600s;' \
    'proxy_send_timeout 3600s;' \
    | docker exec -i "$NPM_CONTAINER" sh -c \
        "mkdir -p /data/nginx/custom && cat >> '$CUSTOM_FILE'"

docker exec "$NPM_CONTAINER" sh -c 'nginx -t' \
    || die "nginx не принял конфигурацию — проверь $CUSTOM_FILE"

docker exec "$NPM_CONTAINER" sh -c 'nginx -s reload'
echo "✓ NPM перезагружен, таймауты WebSocket: 3600s."
echo "  Новые соединения агентов больше не должны рваться каждые 90 секунд."
