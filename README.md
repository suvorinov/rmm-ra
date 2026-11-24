# RMM-RA: MeshCentral + RustDesk (self-hosted)

Локальный RMM-стенд в Docker: удалённое управление, инвентаризация и поддержка
рабочих станций. Доступ к веб-панели — через общий реверс-прокси
`nginx-proxy-manager` (NPM), наружу опубликован только он.

## Состав

| Сервис | Контейнер | Доступ |
|---|---|---|
| MeshCentral | `meshcentral` | `https://rmm.zr.local` (через NPM) |
| RMM Monitor | `rmm-monitor` | `https://mon.zr.local` (через NPM), отдельный compose |
| RustDesk rendezvous | `rustdesk-hbbs` | TCP/UDP `21115-21116`, TCP `21118` |
| RustDesk relay | `rustdesk-hbbr` | TCP `21117`, `21119` |
| NPM | `nginx-proxy` | общий, вне проекта |

Все контейнеры проекта — во внешней сети `zr_admin_net`. Мониторинг поднимается
отдельным файлом `docker-compose.monitor.yml` и использует ту же сеть;
основной стек при этом не меняется.

## Репозитории

| Репозиторий | Что внутри |
|---|---|
| [rmm-ra](https://github.com/suvorinov/rmm-ra) | этот проект: серверная часть (MeshCentral, RustDesk hbbs/hbbr, NPM) и скрипты обслуживания |
| [ansible_project](https://github.com/suvorinov/ansible_project) | Ansible-роли и обёртки для раскатки агентов MeshCentral и клиентов RustDesk на парк Windows/Linux |

## Быстрый старт

```bash
git clone git@github.com:suvorinov/rmm-ra.git
cd rmm-ra
cp .env.example .env   # при первом развёртывании
make up        # запустить стек
make status    # статус контейнеров
make logs      # все логи
make logs-mc   # только MeshCentral
make logs-rd   # только RustDesk
make down      # остановить (данные сохраняются)
```

## Важно: как устроен TLS

TLS терминируется на NPM, а MeshCentral работает с `tlsOffload`. Это значит:

- MeshCentral слушает **обычный HTTP на порту 443 внутри контейнера** (это не опечатка);
- в NPM для `rmm.zr.local` должно быть: **Scheme = `http`**, Forward Host = `meshcentral`, Port = `443`, Websockets = on.

Если поставить в NPM `https` — получите `502 Bad Gateway` и в логах NPM
`SSL_do_handshake() failed ... wrong version number`.

В `meshcentral-data/config.json` за это отвечают:

```json
"tlsOffload": "nginx-proxy",
"trustedProxy": "nginx-proxy"
```

`nginx-proxy` резолвится через Docker DNS, поэтому IP контейнера NPM может меняться.

### Сертификат NPM и агенты

Агент проверяет TLS-сертификат, который видит, и сообщает его SHA384 серверу.
При TLS-offload агент видит сертификат **NPM**, а MeshCentral по умолчанию ждёт
свой. Если в логах есть:

```
Agent bad web cert hash ... holding connection
```

— значит хэш сертификата NPM не прописан в MeshCentral. Исправляется одной
командой (проверка хэша при этом **не отключается**):

```bash
make pin-cert
```

Скрипт считает SHA384 сертификата из NPM, прописывает его в
`domains[""].certhash` конфига MeshCentral, делает бэкап и перезапускает
контейнер. Запускать повторно после **каждой замены/продления сертификата**
в NPM.

Проверить без изменений:

```bash
./scripts/pin-npm-cert.sh --check
```

### WebSocket и таймауты NPM

Вторая причина «пропавших» устройств — дефолтный `proxy_read_timeout 90s`
в NPM. Агент MeshCentral держит WebSocket и может молчать дольше 90 секунд;
NPM закрывает соединение, агент переподключается, устройство мигает
online/offline. В access-логе NPM это видно как `GET /agent.ashx` (101)
примерно раз в 90 секунд.

Лечится один раз (файл переживает перезапуск NPM и не сбрасывается из UI):

```bash
make npm-ws-timeout
```

Скрипт добавляет в `/data/nginx/custom/server_proxy.conf` (NPM подключает его
в каждый проксируемый `server{}`) `proxy_read_timeout 3600s` и
`proxy_send_timeout 3600s`, проверяет конфиг через `nginx -t` и перезагружает
NPM. Уже заданные в этом файле таймауты не перезаписываются.

Проверить состояние обоих пунктов и устройств одной командой:

```bash
make mesh-doctor
```

## Источник истины конфигурации

В `.env` стоит `MC_DYNAMIC_CONFIG=false`, поэтому entrypoint **не** применяет
переменные `MC_ALLOW_NEW_ACCOUNTS` и т.п. Настройки MeshCentral читаются из
`meshcentral-data/config.json`. Меняете настройки — меняйте файл и делайте
`docker compose restart meshcentral`.

## Первый вход и безопасность

1. Открыть `https://rmm.zr.local`, создать первого администратора
   (первый аккаунт автоматически становится site administrator).
2. Сразу включить 2FA.
3. Закрыть регистрацию: `make lock-registration`
   (ставит `NewAccounts=false` в конфиге и перезапускает MeshCentral).
4. Приватные ключи в `meshcentral-data/*private.key` и
   `rustdesk-data/id_ed25519` должны иметь права `600`.
5. Админка NPM (порт `81`) не должна быть доступна из интернета.
6. Сертификат `rmm.zr.local` выписан внутренним УЦ `ZR-ADMIN Root CA` —
   корневой сертификат нужно добавить в доверенные на клиентских машинах.

## Массовая установка агентов (Ansible)

Агенты на парк Windows/Linux-машин ставятся Ansible-ролью из соседнего проекта
[ansible_project](https://github.com/suvorinov/ansible_project) — локально это
`~/working/ansible_project`, там же inventory `windows_hosts.yml`.

1. Создать группу устройств в UI MeshCentral (**Мои устройства → Добавить
   группу устройств**).
2. Узнать её ID:
   ```bash
   make mesh-groups        # имя + meshid
   ```
3. Прогнать установку:
   ```bash
   cd ~/working/ansible_project
   ./deploy_mesh_agent.sh --mesh-id <MESHID> --limit 192.168.0.90 --check
   ./deploy_mesh_agent.sh --mesh-id <MESHID> --limit 192.168.0.90
   ./deploy_mesh_agent.sh --mesh-id <MESHID>   # весь флот
   ```

Подробно — [MESHCENTRAL_AGENT.md](https://github.com/suvorinov/ansible_project/blob/main/MESHCENTRAL_AGENT.md).

## Мониторинг парка (RMM Monitor)

Небольшой FastAPI-сервис со SQLite и серверными страницами: дашборд по хостам,
нагрузка/процессы/запущенные браузеры, журнал задач. Агентов и Prometheus не
добавляет — ходит в MeshCentral через `meshctrl` (инвентарь раз в минуту, снимки
раз в 5 минут, с параллелизмом 4). История и закладки **не собираются**.

```bash
cp monitor/monitor.env.example monitor/monitor.env
chmod 600 monitor/monitor.env
# вписать MON_MC_USER / MON_MC_PASS сервисной учётки и MON_AUTH_PASS
make monitor-up       # образ + контейнер (порт 8000 только внутри docker-сети)
make monitor-status   # ps + healthz
make monitor-logs
make monitor-health
make monitor-backup   # SQLite отдельно от общего бэкапа
```

В NPM нужно создать Proxy Host `mon.zr.local` → `http://monitor:8000`
(Websockets off, SSL от того же внутреннего УЦ). Для разработки без Docker —
`make monitor-dev`, сервис слушает `0.0.0.0:8000` и доступен по
`http://192.168.0.9:8000`. Подробно — [MONITORING.md](MONITORING.md):
учётка MeshCentral, переменные, API, приватность, локальный запуск и диагностика.
Секреты (`monitor/monitor.env`, `monitor/dev.env`) и БД (`monitor-data/`,
`monitor-data-dev/`) в git не попадают.

## 2FA для администратора

1. Войти в `https://rmm.zr.local` → **My Account** / **Мой аккаунт**.
2. Открыть **Account security** / **Безопасность аккаунта**.
3. **Manage authenticator app** / **Управление приложением для проверки
   подлинности** → отсканировать QR-код в Google/Microsoft Authenticator →
   ввести 6-значный код → подтвердить.
4. Сразу открыть **Manage backup codes** / **Управление резервными кодами** →
   сгенерировать 10 одноразовых кодов и сохранить их **вне сервера**.
5. Перелогиниться и убедиться, что токен запрашивается.

⚠️ Админ один, поэтому резервные коды критичны: без них потеря телефона
означает потерю доступа. SMTP на сервере не настроен, поэтому 2FA по почте
недоступна — используйте приложение + резервные коды.

Если доступ всё-таки потерян:

```bash
make reset-2fa                              # показать состояние 2FA
./scripts/reset-2fa.sh --apply --yes        # снять все вторые факторы
```

Скрипт останавливает MeshCentral, делает бэкап БД (`meshcentral.db.bak-*`),
удаляет `otpsecret`/`otpkeys`/`otphkeys`/`otpekey`/`otpduo`/`otpdev` у
`user//suvorinov`, уплотняет NeDB и поднимает контейнер обратно.

## Клиенты RustDesk

Сервер (`hbbs`/`hbbr`) уже работает. Клиентам нужны только адрес и публичный
ключ — их показывает:

```bash
make rustdesk-info      # адрес, ключ, порты, число подключённых пиров
```

Массовая установка — ролью из
[ansible_project](https://github.com/suvorinov/ansible_project)
(локально `~/working/ansible_project`, та же обёртка, что и для MeshCentral):

```bash
cd ~/working/ansible_project
./deploy_rustdesk.sh --prefetch       # один раз: положить установщики в кэш
./deploy_rustdesk.sh --limit 192.168.0.143 --check
./deploy_rustdesk.sh --limit 192.168.0.143
./deploy_rustdesk.sh                 # весь парк, спросит подтверждение
```

Обёртка сама берёт `SERVER_IP` из `.env` и ключ из
`rustdesk-data/id_ed25519.pub`. Установщики из `files/installers/`
раздаются клиентам по WinRM (GitHub на клиентах не нужен). ID каждой машины
сохраняется в `reports/rustdesk/<host>.txt`.

Постоянный пароль для unattended-доступа:

```bash
./deploy_rustdesk.sh --limit 192.168.0.143 --ask-password
./deploy_rustdesk.sh --ask-password    # на весь парк одним паролем
```

Роль кладёт его в конфиг службы и включает `verification-method=use-permanent-password`
(одноразовый пароль с экрана клиента больше не нужен). Подключиться, не вводя
пароль руками, можно штатным CLI:

```powershell
rustdesk.exe --connect 511788413 --password 'ТвойПароль'
```

В OSS-версии совсем без пароля нельзя: hbbs только сводит пиров, пароль
проверяет сам удалённый хост.

### Адресная книга (HTML)

Чтобы не искать ID по файлам, собери автономную страницу:

```bash
make rustdesk-book
# другой каталог отчётов:
./scripts/rustdesk-book.sh --reports ~/working/ansible_project/reports/rustdesk
```

Результат — `reports/rustdesk-book.html`: таблица «хост — ID — версия — есть ли
постоянный пароль», фильтр по IP/ID/версии, переключатель «только без пароля»,
кликабельные ссылки `rustdesk://connection/new/<ID>` и кнопка «скопировать
команду» `rustdesk.exe --connect <ID>`. Хосты без постоянного пароля подсвечены,
их список выводится сверху.

Каталог `reports/` в git не попадает — там внутренние IP и ID. Страница
собирается из отчётов `deploy_rustdesk.sh`
(`~/working/ansible_project/reports/rustdesk/*.txt`), пароли в неё не пишутся.

Подробно — [RUSTDESK_CLIENT.md](https://github.com/suvorinov/ansible_project/blob/main/RUSTDESK_CLIENT.md).

## Бэкап и восстановление

```bash
make backup     # ./backups/rmm-ra-YYYYmmdd-HHMMSS.tar.gz (chmod 600)
```

В архив попадают `docker-compose.yml`, `docker-compose.monitor.yml`, `.env`,
`Makefile`, `README.md`, `MONITORING.md`, `.gitignore`, `scripts/`,
`meshcentral-data/`, `rustdesk-data/`.

БД мониторинга в общий архив **не входит** (там имена пользователей и хостов):
```bash
make monitor-backup   # ./backups/monitor-YYYYmmdd-HHMMSS.db (chmod 600)
```

Восстановление (стек остановлен):

```bash
make down
tar xzf backups/rmm-ra-YYYYmmdd-HHMMSS.tar.gz -C .
make up
```

**Критично:** `rustdesk-data/id_ed25519` — идентичность RustDesk-сервера.
Без него придётся перенастраивать всех клиентов. Храните копию вне сервера.

## Полезное

```bash
make rustdesk-info  # адрес, ключ, порты и число пиров RustDesk
make rustdesk-key   # только публичный ключ для настройки клиентов
make mesh-groups    # ID групп устройств MeshCentral (для установки агентов)
make mesh-doctor    # проверка сертификата, таймаутов NPM и устройств
make monitor-build  # пересобрать образ мониторинга
make monitor-up     # поднять мониторинг (нужен monitor/monitor.env)
make monitor-health # healthz контейнера rmm-monitor
make monitor-dev    # запустить без Docker: http://192.168.0.9:8000
make monitor-dev-test  # pytest локально, без контейнера
make pull           # скачать свежие образы
make update         # обновить и перезапустить
```

## Диагностика

```bash
make mesh-doctor         # сертификат, таймауты NPM, состояние устройств
docker logs meshcentral --tail 100
docker exec nginx-proxy tail -n 50 /data/logs/proxy-host-3_error.log
docker exec nginx-proxy tail -f /data/logs/proxy-host-3_access.log
curl -sk -o /dev/null -w '%{http_code}\n' --resolve rmm.zr.local:443:127.0.0.1 https://rmm.zr.local/
```

Если устройства не видны в «Мои устройства»:

- `make mesh-doctor` — покажет рассинхрон `certhash` и таймауты WebSocket;
- в UI сбрось фильтр устройств на **«Все»** (он запоминается в браузере,
  и при выбранном «В сети» offline-устройства скрыты);
- `make mesh-groups` — проверь, что агенты ставятся в нужную группу.
