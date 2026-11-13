#!/usr/bin/env bash
# ============================================================
#  mesh-id.sh - показать ID групп устройств MeshCentral
#
#  Читает базу NeDB meshcentral-data/meshcentral.db и печатает
#  список групп устройств с их meshid. Именно этот ID нужно
#  передавать Ansible-роли как mc_mesh_id.
#
#  Использование:
#    ./scripts/mesh-id.sh              # имя + ID
#    ./scripts/mesh-id.sh --raw        # только ID (для скриптов)
#    ./scripts/mesh-id.sh --name Имя   # найти группу по имени (подстрока)
#
#  Переменные окружения:
#    MC_DB   - путь к meshcentral.db (по умолчанию ../meshcentral-data/...)
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB="${MC_DB:-${SCRIPT_DIR}/../meshcentral-data/meshcentral.db}"

RAW=0
FILTER=""

usage() {
    sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --raw|-r)   RAW=1; shift ;;
        --name|-n)  FILTER="${2:-}"; shift 2 ;;
        --help|-h)  usage 0 ;;
        *) echo "Неизвестный аргумент: $1" >&2; usage 1 ;;
    esac
done

if [[ ! -r "$DB" ]]; then
    echo "ОШИБКА: база не найдена или недоступна для чтения: $DB" >&2
    exit 1
fi

python3 - "$DB" "$RAW" "$FILTER" <<'PY'
import json
import sys

db_path, raw, filt = sys.argv[1], sys.argv[2] == "1", sys.argv[3].lower()

groups = []
with open(db_path, encoding="utf-8", errors="replace") as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        _id = str(obj.get("_id", ""))
        if not _id.startswith("mesh/"):
            continue
        parts = _id.split("/")
        if len(parts) < 3 or not parts[2]:
            continue
        mesh_id = parts[2]
        name = obj.get("name") or "(без имени)"
        mtype = obj.get("mtype")
        if filt and filt not in name.lower():
            continue
        groups.append((name, mesh_id, mtype))

if not groups:
    if filt:
        print(f"Группы с именем, содержащим «{filt}», не найдены.", file=sys.stderr)
    else:
        print("Групп устройств пока нет.", file=sys.stderr)
        print("Создай группу в UI MeshCentral: «Мои устройства» -> «Добавить группу устройств».", file=sys.stderr)
    sys.exit(2)

if raw:
    for _, mesh_id, _ in groups:
        print(mesh_id)
else:
    width = max(len(n) for n, _, _ in groups)
    print(f"{'ГРУППА'.ljust(width)}  MESHID")
    print(f"{'-' * width}  {'-' * 32}")
    for name, mesh_id, mtype in groups:
        suffix = f"  (тип: {mtype})" if mtype is not None else ""
        print(f"{name.ljust(width)}  {mesh_id}{suffix}")
PY
