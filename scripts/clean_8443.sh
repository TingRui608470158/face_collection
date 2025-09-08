#!/usr/bin/env bash
set -euo pipefail
pids=$(ss -ltnp | awk '/:8443/{print $NF}' | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | sort -u)
if [[ -z "${pids}" ]]; then
  echo "No process is listening on 8443"
  exit 0
fi
echo "Killing PIDs: ${pids}"
for pid in ${pids}; do
  kill "$pid" 2>/dev/null || true
done
sleep 1
# force if still there
pids2=$(ss -ltnp | awk '/:8443/{print $NF}' | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | sort -u)
if [[ -n "${pids2}" ]]; then
  echo "Force killing: ${pids2}"
  for pid in ${pids2}; do
    kill -9 "$pid" 2>/dev/null || true
  done
fi
ss -ltnp | grep :8443 || echo "Port 8443 is free"
