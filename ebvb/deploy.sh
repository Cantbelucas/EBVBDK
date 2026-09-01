#!/usr/bin/env bash
# Deploy af EBVB. Til gentagne deploys efter det forste skifte -
# selve omlaegningen fra Nextcloud staar i DEPLOY.md.
#
#   ./deploy.sh lucas@dinserver
#
set -euo pipefail

HOST="${1:-}"
REMOTE="${2:-/opt/ebvb}"

if [ -z "$HOST" ]; then
  echo "Brug: ./deploy.sh <bruger@server> [sti]   (default sti: /opt/ebvb)" >&2
  exit 1
fi

cd "$(dirname "$0")"

echo "==> Sender koden til $HOST:$REMOTE"
# --exclude data er ikke til forhandling: uden den overskriver du
# serverens database og lydfiler med din lokale.
rsync -av --delete \
      --exclude data \
      --exclude __pycache__ \
      --exclude '*.pyc' \
      ./ "$HOST:$REMOTE/"

echo "==> Bygger og starter"
ssh "$HOST" "cd '$REMOTE' && docker compose up -d --build"

echo "==> Tjekker at den svarer"
if ssh "$HOST" "curl -sf -o /dev/null -w '%{http_code}' http://127.0.0.1:8090/" \
   | grep -qE '^(200|302)$'; then
  echo "    OK"
else
  echo "    Den svarer ikke som forventet. Log:" >&2
  ssh "$HOST" "cd '$REMOTE' && docker compose logs --tail 40" >&2
  exit 1
fi

echo "Faerdig."
