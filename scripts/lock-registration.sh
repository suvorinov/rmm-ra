#!/usr/bin/env bash
# Закрывает самостоятельную регистрацию новых аккаунтов в MeshCentral.
# Запускать ПОСЛЕ того, как создан первый администратор.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "Закрываю регистрацию новых аккаунтов..."
docker exec meshcentral sh -c 'jq '\''.domains[""].NewAccounts = false'\'' /opt/meshcentral/meshcentral-data/config.json > /tmp/mc-config.json && mv /tmp/mc-config.json /opt/meshcentral/meshcentral-data/config.json'

sed -i 's/^MC_ALLOW_NEW_ACCOUNTS=.*/MC_ALLOW_NEW_ACCOUNTS=false/' .env

docker compose -f docker-compose.yml restart meshcentral

echo "Готово: NewAccounts=false, MC_ALLOW_NEW_ACCOUNTS=false."
