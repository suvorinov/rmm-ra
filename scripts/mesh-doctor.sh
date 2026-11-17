#!/usr/bin/env bash
# ============================================================
#  mesh-doctor.sh - быстрая проверка MeshCentral за NPM
#
#  Проверяет три вещи, из-за которых устройства "не появляются"
#  или мигают online/offline:
#    1. контейнер meshcentral запущен и healthy;
#    2. certhash в конфиге совпадает с сертификатом NPM
#       (иначе агенты получают "bad web cert hash");
#    3. в NPM увеличен proxy_read_timeout (иначе WebSocket
#       агента рвётся каждые 90 секунд).
#  Дополнительно показывает устройства и их последнее
#  подключение по базе MeshCentral.
#
#  Использование:
#    ./scripts/mesh-doctor.sh
#    make mesh-doctor
# ============================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MC_CONTAINER="${MC_CONTAINER:-meshcentral}"
DB="${MC_DB:-$PROJECT_DIR/meshcentral-data/meshcentral.db}"

GREEN=$'\033[0;32m'
RED=$'\033[0;31m'
YELLOW=$'\033[1;33m'
NC=$'\033[0m'

FAIL=0
ok()   { echo "  ${GREEN}✓${NC} $*"; }
bad()  { echo "  ${RED}✗${NC} $*"; FAIL=1; }
warn() { echo "  ${YELLOW}!${NC} $*"; }

echo "== MeshCentral =="

STATUS="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$MC_CONTAINER" 2>/dev/null || echo missing)"
if [[ "$STATUS" == "healthy" || "$STATUS" == "running" ]]; then
    ok "контейнер $MC_CONTAINER: $STATUS"
else
    bad "контейнер $MC_CONTAINER: $STATUS (docker logs $MC_CONTAINER)"
fi

echo
echo "== Сертификат NPM и certhash =="
CERT_OUT="$("$SCRIPT_DIR/pin-npm-cert.sh" --check 2>&1)"
CERT_RC=$?
echo "$CERT_OUT" | sed 's/^/  /'
if [[ $CERT_RC -eq 0 ]]; then
    ok "certhash актуален"
else
    bad "certhash не совпадает с сертификатом NPM — выполни: make pin-cert"
fi

echo
echo "== WebSocket-таймауты NPM =="
WS_OUT="$("$SCRIPT_DIR/npm-ws-timeout.sh" --check 2>&1)"
WS_RC=$?
echo "$WS_OUT" | sed 's/^/  /'
if [[ $WS_RC -eq 0 ]]; then
    ok "таймауты WebSocket заданы"
else
    bad "NPM рвёт WebSocket на 90s — выполни: make npm-ws-timeout"
fi

echo
echo "== Устройства =="
if [[ -r "$DB" ]]; then
    python3 - "$DB" <<'PY'
import json
import sys
import time

db = sys.argv[1]
nodes = {}
last = {}

with open(db, encoding="utf-8", errors="replace") as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("type") == "node":
            nodes[obj["_id"]] = obj
        elif obj.get("type") == "lastconnect":
            last[obj["_id"][2:]] = obj

now = time.time()
online = offline = never = 0
for nid, node in sorted(nodes.items(), key=lambda kv: kv[1].get("name", "")):
    name = node.get("name") or nid
    lc = last.get(nid)
    if lc is None:
        never += 1
        print(f"  -  {name:10} ни разу не подключалось")
        continue
    ago = int(now - lc.get("time", 0) / 1000)
    if lc.get("cause") == 1 and ago < 900:
        online += 1
        print(f"  +  {name:10} в сети, подключено {ago}с назад")
    else:
        offline += 1
        print(f"  .  {name:10} не в сети, последнее событие {ago}с назад")

print()
print(f"  всего: {len(nodes)} | в сети: {online} | не в сети: {offline} | без подключений: {never}")
PY
else
    warn "база не найдена: $DB"
fi

echo
if [[ $FAIL -eq 0 ]]; then
    echo "${GREEN}Проверка пройдена.${NC} Если устройств всё ещё нет в UI — обнови страницу"
    echo "и сбрось фильтр «Мои устройства» → «Все» (фильтр запоминается в браузере)."
else
    echo "${RED}Есть проблемы — смотри рекомендации выше.${NC}"
fi

exit "$FAIL"
