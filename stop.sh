#!/usr/bin/env bash
# Stops Brain: kill switch (agents + sandbox containers), then terminates the server process.
set -uo pipefail
cd "$(dirname "$0")"

set -a; [ -f .env ] && . ./.env; set +a
PORT="${BRAIN_PORT:-8000}"
HOST="${BRAIN_HOST:-127.0.0.1}"
PODMAN="${BRAIN_PODMAN:-podman}"
CONN="${BRAIN_PODMAN_CONNECTION:-}"

curl -s -m 15 -X POST "http://${HOST}:${PORT}/api/v1/control/kill" >/dev/null && echo "kill switch sent" || echo "server not reachable (already stopped?)"

if pgrep -f "brain.main" >/dev/null; then
  pkill -TERM -f "brain.main"
  for _ in $(seq 1 10); do pgrep -f "brain.main" >/dev/null || break; sleep 1; done
  pgrep -f "brain.main" >/dev/null && pkill -KILL -f "brain.main"
  echo "server stopped"
fi

# Remove any sandbox container left behind (label set by Brain).
ARGS=(); [ -n "$CONN" ] && ARGS=(--connection "$CONN")
IDS=$("$PODMAN" ${ARGS[@]+"${ARGS[@]}"} ps -aq --filter label=brain=1 2>/dev/null)
[ -n "$IDS" ] && "$PODMAN" ${ARGS[@]+"${ARGS[@]}"} rm -f $IDS >/dev/null && echo "sandbox containers removed"
echo "Brain stopped."
