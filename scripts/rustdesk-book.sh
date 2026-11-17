#!/usr/bin/env bash
# ============================================================
#  rustdesk-book.sh - HTML-«адресная книга» из отчётов Ansible
#
#  Читает reports/rustdesk/<host>.txt (их пишет deploy_rustdesk.sh)
#  и собирает автономную HTML-страницу: хост, RustDesk ID, версия,
#  есть ли постоянный пароль, ссылки rustdesk://connection/new/<ID>
#  и команда для CLI. Ничего не отправляет наружу, паролей не хранит.
#
#  Использование:
#    ./scripts/rustdesk-book.sh
#    ./scripts/rustdesk-book.sh --reports ~/working/ansible_project/reports/rustdesk
#    ./scripts/rustdesk-book.sh --out /tmp/book.html
#
#  По умолчанию отчёты ищутся в ./reports/rustdesk, затем в
#  ~/working/ansible_project/reports/rustdesk. Результат —
#  ./reports/rustdesk-book.html (каталог reports/ не коммитится).
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

REPORTS_DIR=""
OUT_FILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --reports) REPORTS_DIR="${2:-}"; shift 2 ;;
        --out)     OUT_FILE="${2:-}";     shift 2 ;;
        -h|--help)
            sed -n '2,22p' "${BASH_SOURCE[0]}" | sed 's/^#  \{0,1\}//'
            exit 0 ;;
        *) echo "Неизвестный аргумент: $1" >&2; exit 2 ;;
    esac
done

if [[ -z "$REPORTS_DIR" ]]; then
    for d in "$PROJECT_DIR/reports/rustdesk" "$HOME/working/ansible_project/reports/rustdesk"; do
        if [[ -d "$d" ]]; then REPORTS_DIR="$d"; break; fi
    done
fi

if [[ -z "$REPORTS_DIR" || ! -d "$REPORTS_DIR" ]]; then
    echo "Не найден каталог отчётов: ${REPORTS_DIR:-<пусто>}" >&2
    echo "Укажи его: ./scripts/rustdesk-book.sh --reports DIR" >&2
    exit 1
fi

[[ -n "$OUT_FILE" ]] || OUT_FILE="$PROJECT_DIR/reports/rustdesk-book.html"
mkdir -p "$(dirname "$OUT_FILE")"

REPORTS_DIR="$REPORTS_DIR" OUT_FILE="$OUT_FILE" python3 - <<'PY'
import html
import os
import re
import sys
import time
from pathlib import Path

reports_dir = Path(os.environ["REPORTS_DIR"])
out_file = Path(os.environ["OUT_FILE"])

hosts = {}

for path in sorted(reports_dir.glob("*.txt")):
    data = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip().lower()] = value.strip().rstrip(",").strip()

    host = data.get("host") or path.stem
    if not host:
        continue

    rid = data.get("rustdesk_id", "")
    rid = re.sub(r"\D", "", rid)

    entry = {
        "host": host,
        "id": rid,
        "version": data.get("version", ""),
        "password": data.get("permanent_password", "").lower(),
        "mtime": path.stat().st_mtime,
    }
    # один файл на хост; если дубликаты — берём самый свежий
    prev = hosts.get(host)
    if prev is None or entry["mtime"] >= prev["mtime"]:
        hosts[host] = entry


def ip_key(host):
    try:
        return (0, tuple(int(p) for p in host.split(".")))
    except ValueError:
        return (1, host)


rows = sorted(hosts.values(), key=lambda e: ip_key(e["host"]))
total = len(rows)
with_pw = sum(1 for r in rows if r["password"] == "yes")
without_pw = [r for r in rows if r["password"] == "no"]
unknown_pw = [r for r in rows if r["password"] not in ("yes", "no")]
no_id = [r for r in rows if not r["id"]]


def badge(value):
    if value == "yes":
        return '<span class="badge ok">да</span>'
    if value == "no":
        return '<span class="badge warn">нет</span>'
    return '<span class="badge unknown">н/д</span>'


def esc(value):
    return html.escape(str(value), quote=True)


body_rows = []
for r in rows:
    host = esc(r["host"])
    rid = esc(r["id"])
    version_raw = esc(r["version"])
    version = version_raw or '<span class="muted">—</span>'
    if r["id"]:
        id_cell = (
            f'<a class="id" href="rustdesk://connection/new/{rid}" '
            f'title="Открыть {rid} в RustDesk">{rid}</a>'
        )
        cmd = esc(f'rustdesk.exe --connect {r["id"]}')
        actions = (
            f'<button class="copy" data-copy="{cmd}" title="Скопировать команду">'
            f'копировать</button>'
        )
    else:
        id_cell = '<span class="muted">ID не получен</span>'
        actions = ""

    cls = ' class="nopw"' if r["password"] == "no" else ""
    body_rows.append(
        f'<tr{cls} data-host="{host}" data-id="{rid}" data-version="{version_raw}">'
        f"<td class=\"host\">{host}</td>"
        f"<td>{id_cell}</td>"
        f"<td class=\"ver\">{version}</td>"
        f"<td>{badge(r['password'])}</td>"
        f"<td class=\"actions\">{actions}</td>"
        f"</tr>"
    )

without_list = "".join(
    f"<li><b>{esc(r['host'])}</b> — ID {esc(r['id']) or '—'}</li>" for r in without_pw
)
without_block = (
    f'<div class="alert"><b>Без постоянного пароля: {len(without_pw)}</b>'
    f"<ul>{without_list}</ul>"
    "<div class=\"muted\">Этим хостам нужен прогон "
    "<code>./deploy_rustdesk.sh --limit &lt;host&gt; --ask-password</code></div></div>"
    if without_pw
    else '<div class="alert ok">У всех хостов с отчётами постоянный пароль задан.</div>'
)

if unknown_pw:
    without_block += (
        f'<div class="alert"><b>Нет данных о пароле: {len(unknown_pw)}</b>'
        "<ul>"
        + "".join(f"<li><b>{esc(r['host'])}</b> — отчёт без строки permanent_password</li>" for r in unknown_pw)
        + "</ul><div class=\"muted\">Перезапусти роль: <code>./deploy_rustdesk.sh --limit &lt;host&gt;</code></div></div>"
    )

if no_id:
    without_block += (
        f'<div class="alert"><b>ID не получен: {len(no_id)}</b>'
        "<ul>"
        + "".join(f"<li><b>{esc(r['host'])}</b></li>" for r in no_id)
        + "</ul></div>"
    )

generated = time.strftime("%d.%m.%Y %H:%M")

doc = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RustDesk: адресная книга парка</title>
<style>
  :root {{
    color-scheme: light dark;
    --bg: #f4f6f9; --card: #ffffff; --text: #1c2430; --muted: #6b7785;
    --line: #e3e8ef; --accent: #2563eb; --ok: #0f7b3f; --ok-bg: #e7f6ec;
    --warn: #b45309; --warn-bg: #fff4e5; --unknown-bg: #eef1f5;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #12161c; --card: #1a2029; --text: #e6ebf2; --muted: #93a0b0;
      --line: #2a3440; --accent: #6ea8fe; --ok: #7ee2a8; --ok-bg: #14301f;
      --warn: #ffc078; --warn-bg: #3a2a12; --unknown-bg: #242c37;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 24px; background: var(--bg); color: var(--text);
         font: 15px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
  .wrap {{ max-width: 1100px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .sub {{ color: var(--muted); margin-bottom: 18px; }}
  .cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }}
  .stat {{ background: var(--card); border: 1px solid var(--line); border-radius: 10px;
           padding: 12px 16px; min-width: 150px; }}
  .stat b {{ display: block; font-size: 24px; }}
  .stat span {{ color: var(--muted); font-size: 13px; }}
  .alert {{ background: var(--warn-bg); border: 1px solid var(--line); border-radius: 10px;
            padding: 12px 16px; margin-bottom: 16px; }}
  .alert.ok {{ background: var(--ok-bg); }}
  .alert ul {{ margin: 8px 0 4px; padding-left: 20px; }}
  .toolbar {{ display: flex; gap: 10px; align-items: center; margin: 0 0 12px; flex-wrap: wrap; }}
  input[type=search] {{ flex: 1 1 260px; padding: 9px 12px; border-radius: 8px;
                        border: 1px solid var(--line); background: var(--card); color: var(--text); }}
  label.chk {{ display: flex; gap: 6px; align-items: center; color: var(--muted); }}
  table {{ width: 100%; border-collapse: collapse; background: var(--card);
           border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }}
  th, td {{ padding: 9px 12px; border-bottom: 1px solid var(--line); text-align: left; }}
  th {{ position: sticky; top: 0; background: var(--card); font-size: 13px;
        color: var(--muted); text-transform: uppercase; letter-spacing: .03em; }}
  tr:last-child td {{ border-bottom: none; }}
  tr.nopw td {{ background: var(--warn-bg); }}
  tr.hide {{ display: none; }}
  .host {{ font-weight: 600; white-space: nowrap; }}
  .id {{ font: 600 15px/1 ui-monospace, SFMono-Regular, Consolas, monospace;
         color: var(--accent); text-decoration: none; }}
  .id:hover {{ text-decoration: underline; }}
  .ver {{ font: 13px ui-monospace, SFMono-Regular, Consolas, monospace; color: var(--muted); }}
  .badge {{ display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 12px; font-weight: 600; }}
  .badge.ok {{ color: var(--ok); background: var(--ok-bg); }}
  .badge.warn {{ color: var(--warn); background: var(--warn-bg); }}
  .badge.unknown {{ color: var(--muted); background: var(--unknown-bg); }}
  .actions {{ width: 1%; white-space: nowrap; }}
  .copy {{ font: 12px system-ui, sans-serif; cursor: pointer; padding: 5px 10px;
           border: 1px solid var(--line); border-radius: 7px; background: var(--bg); color: var(--muted); }}
  .copy:hover {{ color: var(--text); border-color: var(--muted); }}
  .muted {{ color: var(--muted); }}
  code {{ font: 12px ui-monospace, SFMono-Regular, Consolas, monospace;
          background: var(--unknown-bg); padding: 2px 5px; border-radius: 5px; }}
  footer {{ color: var(--muted); font-size: 13px; margin-top: 18px; }}
  @media (max-width: 640px) {{ body {{ padding: 12px; }} .ver, .actions {{ display: none; }} }}
</style>
</head>
<body>
<div class="wrap">
  <h1>RustDesk: адресная книга парка</h1>
  <div class="sub">Собрано из отчётов <code>{esc(reports_dir)}</code> — {esc(generated)}.
    Пароли здесь не хранятся; ссылки открывают клиент RustDesk.</div>

  <div class="cards">
    <div class="stat"><b>{total}</b><span>хостов с отчётами</span></div>
    <div class="stat"><b>{with_pw}</b><span>с постоянным паролем</span></div>
    <div class="stat"><b>{len(without_pw)}</b><span>без постоянного пароля</span></div>
    <div class="stat"><b>{len(unknown_pw)}</b><span>без данных о пароле</span></div>
    <div class="stat"><b>{len(no_id)}</b><span>без ID</span></div>
  </div>

  {without_block}

  <div class="toolbar">
    <input type="search" id="q" placeholder="Фильтр: IP, ID или версия…" autocomplete="off">
    <label class="chk"><input type="checkbox" id="only-nopw"> только без пароля</label>
  </div>

  <table>
    <thead>
      <tr><th>Хост</th><th>RustDesk ID</th><th>Версия</th><th>Постоянный пароль</th><th></th></tr>
    </thead>
    <tbody id="rows">
      {''.join(body_rows) if body_rows else '<tr><td colspan="5" class="muted">Отчётов нет</td></tr>'}
    </tbody>
  </table>

  <footer>
    Пересобрать: <code>make rustdesk-book</code> в rmm-ra.
    Отчёты пишет <code>deploy_rustdesk.sh</code> в
    <code>reports/rustdesk/&lt;host&gt;.txt</code>.
    Страница содержит внутренние IP и ID — в git не коммитится.
  </footer>
</div>
<script>
  const q = document.getElementById('q');
  const only = document.getElementById('only-nopw');
  const rows = Array.from(document.querySelectorAll('#rows tr[data-host]'));
  function apply() {{
    const term = q.value.trim().toLowerCase();
    const nopw = only.checked;
    rows.forEach(tr => {{
      const hay = (tr.dataset.host + ' ' + tr.dataset.id + ' ' + tr.dataset.version).toLowerCase();
      const okTerm = !term || hay.includes(term);
      const okPw = !nopw || tr.classList.contains('nopw');
      tr.classList.toggle('hide', !(okTerm && okPw));
    }});
  }}
  q.addEventListener('input', apply);
  only.addEventListener('change', apply);
  document.querySelectorAll('.copy').forEach(btn => {{
    btn.addEventListener('click', async () => {{
      const text = btn.dataset.copy;
      try {{
        await navigator.clipboard.writeText(text);
      }} catch (e) {{
        const ta = document.createElement('textarea');
        ta.value = text; document.body.appendChild(ta); ta.select();
        document.execCommand('copy'); ta.remove();
      }}
      const old = btn.textContent;
      btn.textContent = 'скопировано';
      setTimeout(() => btn.textContent = old, 1200);
    }});
  }});
</script>
</body>
</html>
"""

out_file.write_text(doc, encoding="utf-8")

print(f"Отчётов: {total} | с паролем: {with_pw} | без пароля: {len(without_pw)}"
      f" | без данных: {len(unknown_pw)} | без ID: {len(no_id)}")
for r in without_pw:
    print(f"  без постоянного пароля: {r['host']} (ID {r['id'] or '—'})")
for r in unknown_pw:
    print(f"  нет данных о пароле: {r['host']} (ID {r['id'] or '—'})")
for r in no_id:
    print(f"  не получен ID: {r['host']}")
print(f"Файл: {out_file}")
PY
