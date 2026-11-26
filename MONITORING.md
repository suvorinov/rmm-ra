# Мониторинг парка поверх MeshCentral

Небольшой сервис `RMM Monitor`: дашборд по хостам, нагрузка/процессы/браузеры.
**Новых агентов на хостах не ставим**, Prometheus/Grafana не добавляем.
Единственный канал — MeshCentral: его CLI `meshctrl` (WebSocket API).
Основной стек (`docker-compose.yml`) не меняется — мониторинг живёт в отдельном
`docker-compose.monitor.yml` и в той же docker-сети `zr_admin_net`.

## Что собирается

Раз в минуту — инвентарь (`ListDevices --json`): имя, группа, IP, ОС, версия
агента, online/offline. В MeshCentral после переустановок агентов копятся дубли
с одним hostname; мониторинг оставляет по одному устройству на имя (сначала
онлайн, потом самое свежее, затем уже выбранный node id), чтобы дашборд не
затопили исторические записи.

Раз в 5 минут — компактный снимок Windows-хоста:

- CPU (модель, ядра, загрузка, очередь), RAM, аптайм;
- диски (занято/свободно по томам);
- IPv4-адреса;
- топ процессов по памяти (имя, число экземпляров, память, CPU-время);
- запущенные браузеры и их пользователи;
- интерактивный пользователь и активные сессии.

**Не собирается:** история посещений, закладки, cookie, пароли, содержимое
файлов, кейлоггинг, скриншоты. История/закладки — отдельный режим по запросу
(фаза 2), он описан в `WINDOWS_AUDIT.md` в проекте `ansible_project` и требует
явного уведомления пользователей, срока хранения и RBAC. В дашборд эти данные
не попадают.

Снимок делается PowerShell-скриптом `monitor/app/ps/snapshot.ps1`. Скрипт
**один раз** заливается на хост в `C:\Windows\Temp\rmm-monitor-snapshot-<sha12>.ps1`
через `meshctrl Upload`; дальше только короткая команда
`powershell.exe ... -File ... -Top 25`. Если Temp почистили — сервис заметит
характерную ошибку, перезальёт скрипт и повторит попытку. SHA хранится в БД,
поэтому 7 КБ скрипта не гоняются каждые 5 минут на 160 хостов.

## Развёртывание

### 1. Сервисная учётка MeshCentral

В UI MeshCentral создай отдельного пользователя (например, `svc-monitor`) с
доступом только к нужным группам устройств. Ему нужны права на чтение устройств,
`RunCommand` и `Upload`. Не используй личный админский аккаунт.
Для сервисного аккаунта 2FA лучше не включать — иначе придётся хранить
одноразовый токен в `MON_MC_TOKEN` и обновлять его.

### 2. Переменные окружения

```bash
cd ~/opt/rmm-ra
cp monitor/monitor.env.example monitor/monitor.env
chmod 600 monitor/monitor.env
$EDITOR monitor/monitor.env     # MON_MC_USER / MON_MC_PASS / MON_AUTH_PASS
```

Файл в git не попадает (`.gitignore`). Пароль передаётся в `meshctrl` только
через `MESHCTRL_LOGINPASS` в окружении процесса — не в argv, поэтому его не
видно в `/proc/<pid>/cmdline`. Секреты вырезаются из логов и сообщений об
ошибках (`redact()`), логин-ключ в URL маскируется как `key=***`.

Вместо пароля можно использовать login key:
`MON_MC_URL=ws://meshcentral:443?key=xxxxxxxx`.

Основные переменные (все с префиксом `MON_`):

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `MON_MC_URL` | `ws://meshcentral:443` | WebSocket MeshCentral внутри docker-сети (HTTP, `tlsOffload`) |
| `MON_MC_USER` / `MON_MC_PASS` | — | сервисный аккаунт MeshCentral |
| `MON_MC_TOKEN` | — | 2FA-токен, если он реально нужен |
| `MON_AGENT_SCRIPT_URL` | — | адрес, по которому агенты тянут `snapshot.ps1`; пусто = `meshctrl upload` |
| `MON_AUTH_USER` / `MON_AUTH_PASS` | — | HTTP Basic для веб-интерфейса |
| `MON_ALLOW_ANONYMOUS` | `false` | только для локальной отладки |
| `MON_INVENTORY_INTERVAL` | `60` | инвентаризация, сек |
| `MON_SNAPSHOT_INTERVAL` | `300` | снимки, сек |
| `MON_SNAPSHOT_CONCURRENCY` | `4` | параллельных `meshctrl`-команд |
| `MON_SNAPSHOT_TIMEOUT` | `90` | таймаут одной команды, сек |
| `MON_SNAPSHOT_TOP_PROCESSES` | `25` | сколько процессов в топе |
| `MON_SNAPSHOT_MAX_HOSTS` | `0` | `0` = все; можно ограничить для первого запуска |
| `MON_RETENTION_DAYS` | `30` | хранение снимков |
| `MON_RETENTION_RUNS_DAYS` | `30` | хранение журнала задач |
| `MON_DB_PATH` | `/data/monitor.db` | SQLite (права 0600) |
| `MON_TZ` | `Europe/Moscow` | зона отображения времени (в БД — naive UTC) |
| `MON_TITLE` | `RMM Monitor` | заголовок страниц |

### 3. Подъём

```bash
make monitor-up          # соберёт образ и поднимет контейнер
make monitor-status      # ps + healthz
make monitor-health      # только healthz
make monitor-logs        # логи (Ctrl+C)
```

Контейнер слушает `0.0.0.0:8000` **внутри** docker-сети, наружу порт не
публикуется. Данные — в `./monitor-data` (каталог 0700, БД 0600, в git не
попадает). Контейнер запускается от uid/gid 1000, `read_only: true`, `tmpfs
/tmp`, `cap_drop: ALL`, `no-new-privileges`.

### 4. NPM (nginx-proxy-manager)

Создай Proxy Host `mon.zr.local`:

- **Scheme:** `http`
- **Forward Hostname / IP:** `monitor`
- **Forward Port:** `8000`
- **Websockets:** off (здесь нет WebSocket на клиентской стороне)
- **SSL:** тот же внутренний УЦ `ZR-ADMIN Root CA`, что и для `rmm.zr.local`
- при желании — Access List NPM в addition к HTTP Basic самого сервиса

Не ставь `https` в Scheme: tlsOffload, как у MeshCentral, здесь не используется,
сервис отдаёт обычный HTTP. `proxy_read_timeout 3600s` из
`make npm-ws-timeout` ему не мешает.

Для самих агентов задай `MON_AGENT_SCRIPT_URL` в `monitor.env` — адрес,
доступный Windows-хостам. Два рабочих варианта: `http://192.168.0.9:8000`
(порт опубликован в доверенной LAN) либо `https://mon.zr.local` с тем же
внутренним УЦ, установленным на хостах. Не отключай проверку TLS у агента:
скрипт выполняется от SYSTEM, и подмена по пути даст произвольный код. Если на
NPM включён Access List, путь `/agent/snapshot.ps1` должен быть доступен агенту
без дополнительной авторизации.

Проверка:

```bash
curl -u admin:'...' -sk --resolve mon.zr.local:443:127.0.0.1 \
  https://mon.zr.local/healthz
```

## Интерфейс

- `/` — дашборд: всего/онлайн/устаревшие/ошибки, средняя нагрузка, таблица
  хостов с фильтром по имени/IP/группе/ОС и статусу. Заголовки CPU, RAM, Диск,
  Браузеры, Процессы и Аптайм кликабельны: первый клик сортирует по убыванию,
  второй — по возрастанию, третий возвращает исходный порядок. Клик по строке открывает
  модальное окно с деталями: метрики, диски, браузеры, пользователи и топ
  процессов (данные берутся с `/api/hosts/<id>` без перезагрузки страницы);
- `/host/<id>` — карточка: графики CPU/RAM (SVG, без внешних библиотек), диски,
  браузеры, пользователи, топ процессов, история снимков, кнопка «Снять сейчас»;
- `/runs` — журнал задач (инвентарь, снимки, очистка) и последние подключения
  через RustDesk;
- `/healthz` — состояние БД, планировщика и MeshCentral (без авторизации,
  секретов не содержит).

JSON API (тоже под Basic): `/api/summary`, `/api/hosts`, `/api/hosts/<id>`,
`/api/snapshots/<id>?limit=100`, `/api/rustdesk/connect/<id>?format=json`.

### RustDesk: адресная книга по IP

Источник — `reports/rustdesk-book.html`, который собирается командой
`make rustdesk-book` из отчётов Ansible. В мониторинг попадают только IP,
RustDesk ID, версия и признак «постоянный пароль задан». **Самих паролей
в БД, API и логах нет.**

```bash
make rustdesk-book            # пересобрать HTML из ~/working/ansible_project/reports/rustdesk
make monitor-import-rustdesk  # синхронизировать book → SQLite мониторинга
```

Импорт — полный: исчезнувшие из книги IP удаляются, изменённые ID обновляются.
Кнопка «RustDesk» в строке таблицы и в модалке хоста ведёт не напрямую в
`rustdesk://`, а в `/api/rustdesk/connect/<host_id>`. Эндпоинт проверяет, что
ID состоит только из цифр, пишет событие в аудит (пользователь, хост, IP, ID,
время — без секретов) и делает редирект `302` на
`rustdesk://connection/new/<ID>`. Если записи нет — `404`. Для скриптов можно
добавить `?format=json` и получить тот же адрес в JSON без редиректа.

В контейнере `./reports` смонтирован только для чтения, поэтому импорт можно
запустить и внутри:
`docker exec rmm-monitor python -m app.rustdesk_book --file /app/reports/rustdesk-book.html`.
Старая книга помечается бейджем «книга устарела», если файлу больше 7 суток.

Ручной «Снять сейчас» и «Обновить инвентарь» троттлится (20 и 10 секунд) и
запускается в фоновом потоке; POST защищён проверкой `Origin`/`Referer`.
Страницы отдают заголовки `Content-Security-Policy`, `X-Frame-Options: DENY`,
`X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin` и
`Cache-Control: no-store`.

## Устройство планировщика

`APScheduler` в процессе uvicorn:

- инвентарь — каждые 60 с, сразу при старте;
- снимки — каждые 300 с, через 20 с после старта;
- очистка — ежедневно в 03:30 `MON_TZ`.

У задач `coalesce=True` и `max_instances=1`: если опрос залип, следующий тик
не плодит конкурентов. Снимок берётся только у онлайн-Windows-хостов без
свежего снимка (>80% интервала), с ограничением параллелизма. Результат пишется
в `job_runs`, а ошибка — в `hosts.last_snapshot_error`.

Скрипт снимка доставляется одним коротким `run_command`: агент сам скачивает
`/agent/snapshot.ps1` с адреса `MON_AGENT_SCRIPT_URL` (в dev подставляется
`http://<lan-ip>:8000`) и выполняет его. Причина простая: `meshctrl upload`
передаёт бинарные кадры, и с хоста они до агента **не доходят** (хотя CLI
печатает «Upload done»). Старый путь через загрузку файла остался запасным —
включается, если HTTP-доставка не сработала. Если ответ оборвался или агент
временно не маршрутизируется, сервис делает одну быструю повторную попытку —
такие сбои на живом парке проходят сами за пару секунд. Эндпоинт
`/agent/snapshot.ps1` открыт без Basic: агент не умеет авторизоваться, а сам
скрипт секретов не содержит.

## Приватность и доступ

- В БД хранятся имена хостов, IP, имена пользователей Windows и процессы —
  это уже внутренняя информация, поэтому `monitor-data/` и `monitor.env`
  в git не попадают, а БД закрыта правами 0600.
- Веб-интерфейс закрыт HTTP Basic. Для большего числа пользователей можно
  повесить Access List NPM, но помни: Basic без HTTPS светит паролем —
  используй `mon.zr.local` только через NPM с TLS.
- Ручные экспорты/API отдают те же данные, что и экран; отдельного режима
  «только админ» сейчас нет — не давай ссылку на мониторинг широкому кругу.
- История/закладки Collector не трогает. Если позже включишь аудит браузеров,
  сделай это отдельным выключенным по умолчанию модулем: уведомление
  пользователей, правовое основание, срок хранения, отдельная роль и шифрование
  экспортов.

## Диагностика

```bash
make monitor-status
make monitor-logs
docker exec rmm-monitor python -c "import json,urllib.request; print(json.dumps(json.load(urllib.request.urlopen('http://127.0.0.1:8000/healthz')), ensure_ascii=False, indent=2))"

# что реально делает meshctrl (из контейнера мониторинга):
docker exec -e MESHCTRL_URL=ws://meshcentral:443 -e MESHCTRL_LOGINUSER=svc-monitor \
  rmm-monitor node /opt/meshctrl/meshctrl.js ListDevices --json | head
```

Типичные проблемы:

| Симптом | Причина / лечение |
|---|---|
| `healthz.meshctrl: false` | в образе не нашёлся `meshctrl.js` или `node`; пересобери `make monitor-build` |
| инвентарь в ошибке `Invalid login` | неверный `MON_MC_USER/MON_MC_PASS` или у аккаунта 2FA (нужен `MON_MC_TOKEN`) |
| `ListDevices` пустой | у сервисного аккаунта нет прав на группы устройств |
| снимки `Access denied` | аккаунту не разрешён `RunCommand` на этих устройствах |
| снимки в ошибке `Unable to connect to remote server` | агент не видит `MON_AGENT_SCRIPT_URL`: проверь IP/порт и файрвол между хостом и мониторингом |
| хосты «устарели» | агент офлайн (`make mesh-doctor`), либо NPM закрыл WebSocket |
| `Upload error.` в логах | запасной путь: нет прав на `C:\Windows\Temp` или агент не поддерживает файловую передачу |
| `не разобрал JSON от meshctrl` | строка из meshctrl не массив — проверь, что `--json` не отпал при обновлении CLI |

## Бэкап

```bash
make monitor-backup    # ./backups/monitor-YYYYmmdd-HHMMSS.db, chmod 600
```

Основной `make backup` архивирует только стек RMM; БД мониторинга добавляется
отдельно, чтобы не тащить PII в общий архив.

## Разработка без Docker

Мониторинг можно поднять прямо на хосте (Python 3.12 + Node на хосте), без
контейнера. Приложение слушает `0.0.0.0`, поэтому доступно из локальной сети:

```bash
make monitor-dev                  # 0.0.0.0:8000, подсказка: http://192.168.0.9:8000
# другой порт / другой адрес сети:
make monitor-dev MONITOR_DEV_PORT=8080 MONITOR_DEV_LAN_IP=192.168.0.50
# с автоперезагрузкой и живым опросом MeshCentral:
make monitor-dev MONITOR_DEV_ARGS="--reload"
```

Что делает цель:

1. `monitor-dev-venv` — создаёт `monitor/.venv` и ставит оттуда
   `requirements.txt` + `requirements-dev.txt`.
2. `monitor-dev-vendor` — один раз копирует `meshctrl.js`, `minimist` и `ws`
   из запущенного контейнера `meshcentral` (или из образа) в
   `monitor/vendor/`. Сам запуск уже **не использует Docker**; если Docker
   недоступен, положи эти файлы вручную по тем же путям. Обновить —
   `make monitor-dev-vendor-update`.
3. Поднимает `uvicorn` через `monitor/scripts/dev.py` с локальными путями:
   БД — `monitor-data-dev/monitor.db`, HOME — `monitor-data-dev/home`,
   `MON_MESHCTRL` — `monitor/vendor/meshctrl.js`. Данные и вендор в git не
   попадают.

Отдельно про MeshCentral: в `dev.env` можно оставить docker-адрес
`MON_MC_URL=ws://meshcentral:443`. `dev.py` сам определит IP запущенного
контейнера (read-only `docker inspect`) и подключится напрямую по
`ws://<ip>:443`. Снимки теперь не зависят от бинарных кадров `meshctrl upload`:
агент тянет скрипт по HTTP с `http://<lan-ip>:8000`, поэтому прямой доступ к
контейнеру нужен только для самой WebSocket-команды. Если контейнер не запущен,
остаётся запасной путь `wss://rmm.zr.local` через локальный NPM с подменой DNS
(`monitor/scripts/dev-node-hosts.js`). Режим выбирается переменной
`MON_DEV_MC_MODE=auto|direct|npm`. Свой `MON_MC_URL` подмена не трогает.
Важно: `meshctrl` сам дописывает `/control.ashx`, поэтому путь в URL указывать
не надо (иначе получится `/control.ashx/control.ashx` и пустой ответ).

По умолчанию `MONITOR_DEV_ARGS=--no-scheduler`: интерфейс и API работают, но
MeshCentral не опрашивается (удобно править вёрстку). Для живого сбора убери
флаг **и** строку `MON_SCHEDULER_ENABLED=false` из `monitor/dev.env`
(переопределить на разовый запуск можно так:
`env MON_SCHEDULER_ENABLED=true make monitor-dev MONITOR_DEV_ARGS=`).
Файл окружения выбирается
в порядке `monitor/monitor.env` → `monitor/dev.env` →
`monitor/monitor.env.example`. Для чистого дева можно сделать:

```bash
cp monitor/dev.env.example monitor/dev.env   # dev-логин/пароль, планировщик off
```

Файл `monitor/dev.env` в git не попадает. Если есть `monitor/monitor.env`
с боевыми секретами, он имеет приоритет — не публикуй `http://192.168.0.9:8000`
во внешнюю сеть и помни, что Basic тут идёт по **обычному HTTP** (TLS в
dev-режиме нет).

Если кнопка «Обновить инвентарь» отдаёт `403`, значит браузер прислал Origin,
не совпадающий с `Host` (часто `Origin: null` из-за расширений/прокси). В логе
будет строка `cross-origin POST отклонён: host=... origin=... referer=...`.
Добавь фактический адрес страницы в окружение и перезапусти:

```bash
# monitor/dev.env
MON_ALLOWED_ORIGINS=http://192.168.0.9:8000,http://127.0.0.1:8000
```

Проверка живая: `/healthz` (и короткий алиас `/health`) открываются без Basic.
Для доступа с другой машины поднимай хост только в доверенной сети либо
закрывай порт фаерволом.

Остановка — `Ctrl+C` в терминале. Локальную БД можно сбросить командой
`make monitor-dev-clean`.

Тесты (без Node, MeshCentral и Windows — всё на фейковом клиенте):

```bash
make monitor-dev-test
# или в уже созданном venv:
monitor/.venv/bin/python -m pytest -q monitor/tests
```
