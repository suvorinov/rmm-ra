# RMM-RA: MeshCentral + RustDesk (self-hosted)

Локальный RMM-стенд в Docker: удалённое управление, инвентаризация и поддержка
рабочих станций. Доступ к веб-панели — через общий реверс-прокси
`nginx-proxy-manager` (NPM), наружу опубликован только он.

## Состав

| Сервис | Контейнер | Доступ |
|---|---|---|
| MeshCentral | `meshcentral` | `https://rmm.zr.local` (через NPM) |
| RustDesk rendezvous | `rustdesk-hbbs` | TCP/UDP `21115-21116`, TCP `21118` |
| RustDesk relay | `rustdesk-hbbr` | TCP `21117`, `21119` |
| NPM | `nginx-proxy` | общий, вне проекта |

Все контейнеры проекта — во внешней сети `zr_admin_net`.

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

Подробно — [RUSTDESK_CLIENT.md](https://github.com/suvorinov/ansible_project/blob/main/RUSTDESK_CLIENT.md).

## Бэкап и восстановление

```bash
make backup     # ./backups/rmm-ra-YYYYmmdd-HHMMSS.tar.gz (chmod 600)
```

В архив попадают `docker-compose.yml`, `.env`, `Makefile`, `README.md`,
`scripts/`, `meshcentral-data/`, `rustdesk-data/`.

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
make pull           # скачать свежие образы
make update         # обновить и перезапустить
```

## Диагностика

```bash
docker logs meshcentral --tail 100
docker exec nginx-proxy tail -n 50 /data/logs/proxy-host-3_error.log
curl -sk -o /dev/null -w '%{http_code}\n' --resolve rmm.zr.local:443:127.0.0.1 https://rmm.zr.local/
```
