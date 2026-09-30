#!/bin/bash
# Linux / genel başlatıcı: ./start.sh
cd "$(dirname "$0")" || exit 1
PY=${PYTHON:-python3}
[ -x .venv/bin/python ] || "$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
REQ_HASH=$(sha1sum requirements.txt | cut -d' ' -f1)
if [ "$(cat .venv/.req_hash 2>/dev/null)" != "$REQ_HASH" ]; then
  python -m pip install -q --upgrade pip && python -m pip install -q -r requirements.txt && echo "$REQ_HASH" > .venv/.req_hash
fi
exec python -m app.server "$@"
