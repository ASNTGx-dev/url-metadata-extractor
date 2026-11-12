#!/usr/bin/env bash
# Start the URL Metadata Extractor API on 127.0.0.1:18081.
#
# The operator MUST set RAPIDAPI_PROXY_SECRET before starting:
#   export RAPIDAPI_PROXY_SECRET='<secret from the RapidAPI dashboard>'
#   ./start.sh
# The secret is never stored in this repo. For local testing only, a
# throwaway value may be exported in the shell (see .env.example).
set -euo pipefail
cd "$(dirname "$0")"

if [ -z "${RAPIDAPI_PROXY_SECRET:-}" ]; then
  echo "ERROR: RAPIDAPI_PROXY_SECRET is not set." >&2
  echo "Set it to the secret from the RapidAPI dashboard, then re-run." >&2
  exit 1
fi

exec /home/hatch/workspace/automation/venv/bin/python -m uvicorn app:app \
  --host 127.0.0.1 --port 18081
