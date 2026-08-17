#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

DAYS="${1:-30}"
SUBJECT="${2:-oligosmith-backend}"
[[ "${DAYS}" =~ ^[1-9][0-9]*$ ]] || { printf 'days must be a positive integer\n' >&2; exit 64; }

agentd_compose run --rm --no-deps --entrypoint python agentd -c \
  'import os,sys; from datetime import datetime,timedelta,timezone; import jwt; now=datetime.now(timezone.utc); print(jwt.encode({"sub":sys.argv[2],"iat":now,"exp":now+timedelta(days=int(sys.argv[1]))}, os.environ["MCP_JWT_SECRET"], algorithm=os.environ.get("MCP_JWT_ALGORITHM","HS256")))' \
  "${DAYS}" "${SUBJECT}"
