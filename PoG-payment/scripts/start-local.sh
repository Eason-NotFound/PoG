#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if command -v node >/dev/null 2>&1; then
  PAYMENT_NODE="$(command -v node)"
else
  PAYMENT_NODE="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
fi
if [ ! -x "$PAYMENT_NODE" ]; then
  echo '请安装 Node.js 22.13+（推荐24），然后 npm run setup && npm start。'
  exit 1
fi
"$PAYMENT_NODE" scripts/setup.mjs
exec "$PAYMENT_NODE" --env-file-if-exists=.env src/server.mjs
