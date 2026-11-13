#!/usr/bin/env bash
# ============================================================
#  rustdesk-info.sh - параметры RustDesk-сервера для клиентов
#
#  Показывает адрес hbbs, публичный ключ, состояние портов и
#  количество зарегистрированных пиров. Всё, что нужно для
#  настройки клиентов и для deploy_rustdesk.sh.
#
#  Использование:
#    ./scripts/rustdesk-info.sh           # человекочитаемо
#    ./scripts/rustdesk-info.sh --raw-key # только публичный ключ
#    ./scripts/rustdesk-info.sh --json    # машинный формат
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
KEY_FILE="$PROJECT_DIR/rustdesk-data/id_ed25519.pub"
DB_FILE="$PROJECT_DIR/rustdesk-data/db_v2.sqlite3"

MODE="text"
case "${1:-}" in
    --raw-key) MODE="key" ;;
    --json)    MODE="json" ;;
    --help|-h)
        sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
        exit 0 ;;
    "") ;;
    *) echo "Неизвестный аргумент: $1" >&2; exit 1 ;;
esac

die() { echo "ОШИБКА: $*" >&2; exit 1; }

SERVER=""
if [[ -r "$ENV_FILE" ]]; then
    SERVER="$(sed -n 's/^SERVER_IP=//p' "$ENV_FILE" | head -1 | tr -d '"'"'"' ')"
fi
KEY=""
if [[ -r "$KEY_FILE" ]]; then
    KEY="$(tr -d '[:space:]' < "$KEY_FILE")"
fi

if [[ "$MODE" == "key" ]]; then
    [[ -n "$KEY" ]] || die "нет $KEY_FILE"
    printf '%s\n' "$KEY"
    exit 0
fi

# --- пиры из базы hbbs (только чтение) ---
PEERS="н/д"
if [[ -r "$DB_FILE" ]]; then
    PEERS="$(python3 - "$DB_FILE" <<'PY' 2>/dev/null || echo "н/д"
import sqlite3
import sys

try:
    con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
    print(con.execute("select count(*) from peer").fetchone()[0])
except Exception:
    print("н/д")
PY
)"
fi

# --- порты на хосте ---
port_open() {
    ss -H -lntu 2>/dev/null | awk '{print $5}' | grep -qE "[:.]$1$"
}
PORTS_OK=0
for p in 21115 21116 21117 21118 21119; do
    port_open "$p" && PORTS_OK=$((PORTS_OK + 1))
done

# --- контейнеры ---
cont_state() {
    docker inspect -f '{{.State.Status}}' "$1" 2>/dev/null || echo "нет"
}
HBBS_STATE="$(cont_state rustdesk-hbbs)"
HBBR_STATE="$(cont_state rustdesk-hbbr)"

if [[ "$MODE" == "json" ]]; then
    python3 - "$SERVER" "$KEY" "$PEERS" "$HBBS_STATE" "$HBBR_STATE" "$PORTS_OK" <<'PY'
import json
import sys

server, key, peers, hbbs, hbbr, ports = sys.argv[1:7]


def as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


print(json.dumps({
    "server": server,
    "key": key,
    "relay_port": 21117,
    "peers": as_int(peers),
    "hbbs": hbbs,
    "hbbr": hbbr,
    "ports_open": as_int(ports),
    "ports_total": 5,
}, ensure_ascii=False, indent=2))
PY
    exit 0
fi

echo "RustDesk сервер"
echo "  hbbs        : ${SERVER:-(не задан SERVER_IP в .env)}"
echo "  relay (hbbr): ${SERVER:-(?)}:21117"
echo "  ключ        : ${KEY:-(нет rustdesk-data/id_ed25519.pub)}"
echo "  контейнеры  : hbbs=$HBBS_STATE, hbbr=$HBBR_STATE"
echo "  порты       : открыто $PORTS_OK из 5 (21115-21119)"
echo "  пиров в базе: $PEERS"

echo
echo "Для клиентов (вручную): ID-сервер ${SERVER:-(?)} и ключ выше."
echo "Массовая установка:"
echo "  cd ~/working/ansible_project"
echo "  ./deploy_rustdesk.sh --limit 192.168.0.143 --check"
