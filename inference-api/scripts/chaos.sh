#!/usr/bin/env bash
# Demo de alta disponibilidad (RS-AC14): dispara tráfico continuo, mata una réplica a mitad de
# camino y reporta cuántos requests fallaron. Requiere el stack levantado (`make up`).
set -euo pipefail

URL="${URL:-http://localhost:8080/v1/classify}"
KEY="${KEY:-demo-key-123}"
TOTAL="${TOTAL:-40}"
COMPOSE="$(docker compose version >/dev/null 2>&1 && echo "docker compose" || echo docker-compose)"

victim="$($COMPOSE ps -q api | head -n1)"
ok=0; fail=0
for i in $(seq 1 "$TOTAL"); do
  if [[ "$i" -eq $((TOTAL / 4)) ]]; then
    echo ">>> matando réplica ${victim:0:12} (SIGKILL, sin graceful shutdown)"
    docker kill "$victim" >/dev/null
  fi
  code="$(curl -s -m 10 -o /dev/null -w '%{http_code}' -X POST "$URL" \
    -H "X-API-Key: $KEY" -H 'Content-Type: application/json' -d "{\"input\":\"req $i\"}")"
  if [[ "$code" == "200" ]]; then ok=$((ok + 1)); else fail=$((fail + 1)); echo "  request $i → $code"; fi
  sleep 0.1
done
echo "OK: $ok  ·  fallidos: $fail  (de $TOTAL)"
# `docker kill` cuenta como stop manual (Docker no la reinicia); en Kubernetes el ReplicaSet la
# recrearía solo. Acá la levantamos a mano para mostrar la recuperación.
echo ">>> recuperando la réplica"
docker start "$victim" >/dev/null
sleep 8
$COMPOSE ps api
