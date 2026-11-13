#!/usr/bin/env bash
# ============================================================
#  check_winrm.sh - Подготовка флота Windows-хостов к Ansible
#
#  Сканирует диапазон IP на наличие WinRM:
#    * TCP-проверка портов 5985 (HTTP) и 5986 (HTTPS);
#    * HTTP-запрос к /wsman — живой WinRM без кредов отвечает 401;
#  По результатам формирует inventory.ini для Ansible.
#
#  Использование:
#    ./check_winrm.sh [START] [END]        (по умолчанию 31 190)
#    INVENTORY=hosts.ini ./check_winrm.sh  (задать имя файла inventory)
#
#  Требуется: nc, curl (без curl — только TCP-проверка портов)
# ============================================================

set -euo pipefail

RANGE_START="${1:-31}"
RANGE_END="${2:-190}"
TIMEOUT=1
MAX_PARALLEL=20
INVENTORY="${INVENTORY:-inventory.ini}"

if ! command -v nc >/dev/null 2>&1; then
    echo "[ERROR] nc не найден. Установите netcat." >&2
    exit 1
fi

HAVE_CURL=1
command -v curl >/dev/null 2>&1 || HAVE_CURL=0

# --- Проверка одного хоста: статус|IP|порты|http_code ---
# Признак WinRM: POST /wsman без кредов отвечает 401 (GET отвечает 405,
# пустой POST — 411, поэтому шлём именно POST с телом).
check_host() {
    local ip="$1"
    local ports=() code scheme

    for port in 5985 5986; do
        if nc -z -w "$TIMEOUT" "$ip" "$port" >/dev/null 2>&1; then
            ports+=("$port")
        fi
    done

    if [[ ${#ports[@]} -eq 0 ]]; then
        echo "DOWN|$ip|-|-"
        return 0
    fi

    if [[ "$HAVE_CURL" -eq 1 ]]; then
        for port in "${ports[@]}"; do
            scheme="http"
            [[ "$port" == "5986" ]] && scheme="https"
            code=$(curl -k -s -o /dev/null -m "$TIMEOUT" -w "%{http_code}" \
                -X POST -d "x" "$scheme://$ip:$port/wsman" 2>/dev/null || true)
            if [[ "$code" == "401" ]]; then
                echo "OK|$ip|$port|$code"
                return 0
            fi
        done
        echo "OPEN|$ip|${ports[*]}|no-winrm"
    else
        echo "OPEN|$ip|${ports[*]}|tcp-only"
    fi
}

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

echo "=== Проверка WinRM на хостах 192.168.0.$RANGE_START - 192.168.0.$RANGE_END ==="
echo "Порты: 5985 (HTTP), 5986 (HTTPS) | Таймаут: ${TIMEOUT}с | Параллельность: $MAX_PARALLEL"
[[ "$HAVE_CURL" -eq 0 ]] && echo "[WARN] curl не найден — проверка только TCP, WinRM не подтверждается."
echo ""

# --- Параллельный скан (не более MAX_PARALLEL одновременных проверок) ---
active=0
for ((i=RANGE_START; i<=RANGE_END; i++)); do
    check_host "192.168.0.$i" >"$tmpdir/host_$i" &
    active=$((active + 1))
    if (( active >= MAX_PARALLEL )); then
        wait -n 2>/dev/null || true
        active=$((active - 1))
    fi
done
wait

# --- Сбор результатов ---
ok=()
open=()
down=()
for file in "$tmpdir"/host_*; do
    IFS='|' read -r status ip ports code <"$file"
    case "$status" in
        OK) ok+=("$ip|$ports") ;;
        OPEN) open+=("$ip|$ports") ;;
        *) down+=("$ip") ;;
    esac
done

sort_ip() {
    printf '%s\n' "$@" | sort -t'|' -k1 -V
}

echo "=== Результат ==="
echo "WinRM подтверждён (OK):   ${#ok[@]}"
echo "Порт открыт (OPEN):       ${#open[@]}"
echo "Недоступны (DOWN):        ${#down[@]}"
echo ""

if [[ ${#ok[@]} -gt 0 ]]; then
    echo "=== Хосты с работающим WinRM ==="
    sort_ip "${ok[@]}" | while IFS='|' read -r ip port; do
        printf '  [OK]   %s:%s\n' "$ip" "$port"
    done
fi

if [[ ${#open[@]} -gt 0 ]]; then
    echo ""
    echo "=== Порт открыт, но WinRM не отвечает (HTTP-проверка не прошла) ==="
    sort_ip "${open[@]}" | while IFS='|' read -r ip ports; do
        printf '  [OPEN] %s:%s\n' "$ip" "$ports"
    done
fi

# --- Генерация inventory для Ansible ---
if [[ ${#ok[@]} -gt 0 ]]; then
    {
        echo "; Автосгенерировано: $(date '+%Y-%m-%d %H:%M:%S')"
        echo "; Диапазон: 192.168.0.$RANGE_START - 192.168.0.$RANGE_END"
        echo "[windows]"
        sort_ip "${ok[@]}" | while IFS='|' read -r ip port; do
            echo "$ip ansible_port=$port"
        done
        echo ""
        echo "[windows:vars]"
        echo "ansible_connection=winrm"
        echo "ansible_winrm_transport=ntlm"
        echo "; Для self-signed сертификатов на 5986/HTTPS:"
        echo "ansible_winrm_server_cert_validation=ignore"
        echo "; ЗАПОЛНИТЕ УЧЁТНЫЕ ДАННЫЕ (лучше через ansible-vault):"
        echo "ansible_user=PUT_ADMIN_USER"
        echo "ansible_password=PUT_PASSWORD"
    } >"$INVENTORY"
    echo ""
    echo "Inventory сохранён: $INVENTORY"
    echo "Дополните ansible_user/ansible_password (или зашифруйте ansible-vault encrypt $INVENTORY)."
else
    echo ""
    echo "Хосты с подтверждённым WinRM не найдены — inventory не создан."
    echo "Проверьте, включён ли WinRM на хостах:"
    echo "  Enable-PSRemoting -Force; Set-Item WSMan:\\localhost\\Client\\TrustedHosts * -Force"
fi
