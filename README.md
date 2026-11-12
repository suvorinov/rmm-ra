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

## Быстрый старт

```bash
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
make rustdesk-key   # публичный ключ для настройки клиентов RustDesk
make pull           # скачать свежие образы
make update         # обновить и перезапустить
```

## Диагностика

```bash
docker logs meshcentral --tail 100
docker exec nginx-proxy tail -n 50 /data/logs/proxy-host-3_error.log
curl -sk -o /dev/null -w '%{http_code}\n' --resolve rmm.zr.local:443:127.0.0.1 https://rmm.zr.local/
```
