#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -f .env.local ]; then cp .env.example .env.local; fi
if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
  if [ ! -d node_modules ]; then npm install; fi
  exec npm run dev
fi
POG_RUNTIME="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies"
if [ -x "$POG_RUNTIME/node/bin/node" ] && [ -x "$POG_RUNTIME/bin/fallback/pnpm" ]; then
  PATH="$POG_RUNTIME/node/bin:$PATH"
  export PATH
  if [ ! -d node_modules ]; then "$POG_RUNTIME/bin/fallback/pnpm" install --frozen-lockfile --store-dir .pnpm-store; fi
  exec "$POG_RUNTIME/bin/fallback/pnpm" dev
fi
echo '请先安装 Node.js 22+，再运行 npm install 与 npm run dev。'
exit 1
