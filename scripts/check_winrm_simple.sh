#!/bin/bash
# check_winrm.sh - Проверка наличия WinRM (порт 5985) на хостах

RANGE_START=31
RANGE_END=190
PORT=5985
TIMEOUT=1

echo "=== Проверка WinRM (порт $PORT) на хостах 192.168.0.$RANGE_START-192.168.0.$RANGE_END ==="
echo ""

ACTIVE_HOSTS=()
INACTIVE_HOSTS=()

for i in $(seq $RANGE_START $RANGE_END); do
    IP="192.168.0.$i"
    if nc -zv -w $TIMEOUT $IP $PORT 2>&1 | grep -q "succeeded"; then
        ACTIVE_HOSTS+=("$IP")
        echo "[OK]   $IP:$PORT - WinRM доступен"
    else
        INACTIVE_HOSTS+=("$IP")
    fi
done

echo ""
echo "=== Результат ==="
echo "Доступно: ${#ACTIVE_HOSTS[@]} хостов"
echo "Недоступно: ${#INACTIVE_HOSTS[@]} хостов"
echo ""
echo "Хосты с WinRM:"
printf '%s\n' "${ACTIVE_HOSTS[@]}"