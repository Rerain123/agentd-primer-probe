#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

WAIT_SECONDS=180
if [[ "${1:-}" == "--wait" ]]; then
  WAIT_SECONDS="${2:-}"
elif [[ -n "${1:-}" ]]; then
  printf 'Usage: %s [--wait seconds]\n' "$0" >&2
  exit 64
fi
[[ "${WAIT_SECONDS}" =~ ^[0-9]+$ ]] || { printf '[FAIL] wait must be an integer\n' >&2; exit 64; }

command -v curl >/dev/null 2>&1 || { printf '[FAIL] curl is required\n' >&2; exit 1; }
[[ -f "${AGENTD_ENV_FILE}" ]] || { printf '[FAIL] missing %s\n' "${AGENTD_ENV_FILE}" >&2; exit 1; }

port="$(agentd_read_env AGENTD_PORT 8020)"
bind_address="$(agentd_read_env AGENTD_BIND_ADDRESS 127.0.0.1)"
if [[ "${bind_address}" == "0.0.0.0" || "${bind_address}" == "::" ]]; then
  bind_address="127.0.0.1"
fi
base_url="http://${bind_address}:${port}"
response_dir="$(mktemp -d -t oligosmith-verify-XXXXXX)"
trap 'rm -rf -- "${response_dir}"' EXIT

request_status() {
  local endpoint="$1"
  local output="$2"
  curl --silent --show-error --connect-timeout 3 --max-time 15 \
    --output "${output}" --write-out '%{http_code}' "${base_url}${endpoint}" || true
}

deadline=$((SECONDS + WAIT_SECONDS))
while true; do
  health_code="$(request_status /health "${response_dir}/health.json")"
  ready_code="$(request_status /ready "${response_dir}/ready.json")"
  if [[ "${health_code}" == "200" && "${ready_code}" == "200" ]]; then
    break
  fi
  if (( SECONDS >= deadline )); then
    printf '[FAIL] service not ready after %s seconds (health=%s ready=%s)\n' \
      "${WAIT_SECONDS}" "${health_code:-none}" "${ready_code:-none}" >&2
    [[ -s "${response_dir}/ready.json" ]] && cat "${response_dir}/ready.json" >&2
    printf '\n' >&2
    agentd_compose ps >&2 || true
    agentd_compose logs --tail 100 agentd >&2 || true
    exit 1
  fi
  sleep 3
done
printf '[PASS] /health and /ready\n'

rustfs_enabled="$(agentd_read_env RUSTFS_ENABLED false)"
if [[ "${rustfs_enabled,,}" =~ ^(1|true|yes|on)$ ]]; then
  if ! agentd_compose exec -T agentd python -c \
    'import json; from agentd.api import service; print(json.dumps(service.wf01_v02.artifact_publisher.probe_write(), ensure_ascii=False))'; then
    printf '[FAIL] RustFS endpoint, credentials, or bucket access is unavailable\n' >&2
    exit 1
  fi
  printf '[PASS] RustFS bucket read/write and test-object cleanup\n'
else
  printf '[WARN] RUSTFS_ENABLED=false: HiMat cannot receive shared report files\n'
fi

unauthorized_code="$(
  curl --silent --show-error --connect-timeout 3 --max-time 15 \
    --request POST \
    --header 'Content-Type: application/json' \
    --header 'Accept: application/json, text/event-stream' \
    --data '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
    --output "${response_dir}/unauthorized.json" \
    --write-out '%{http_code}' \
    "${base_url}/mcp" || true
)"
if [[ "${unauthorized_code}" != "401" ]]; then
  printf '[FAIL] /mcp without JWT returned HTTP %s instead of 401\n' "${unauthorized_code:-none}" >&2
  exit 1
fi
printf '[PASS] /mcp rejects missing JWT\n'

token="$(
  agentd_compose exec -T agentd python -c \
    'import os; from datetime import datetime,timedelta,timezone; import jwt; now=datetime.now(timezone.utc); print(jwt.encode({"sub":"deploy-verify","iat":now,"exp":now+timedelta(minutes=10)}, os.environ["MCP_JWT_SECRET"], algorithm=os.environ.get("MCP_JWT_ALGORITHM","HS256")))'
)"
if [[ -z "${token}" ]]; then
  printf '[FAIL] could not create verification JWT\n' >&2
  exit 1
fi

mcp_request() {
  local payload="$1"
  local output="$2"
  curl --silent --show-error --connect-timeout 3 --max-time 30 \
    --request POST \
    --header "Authorization: Bearer ${token}" \
    --header 'Content-Type: application/json' \
    --header 'Accept: application/json, text/event-stream' \
    --header 'MCP-Protocol-Version: 2025-11-25' \
    --data "${payload}" \
    --output "${output}" \
    --write-out '%{http_code}' \
    "${base_url}/mcp" || true
}

initialize_code="$(mcp_request \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"deploy-verify","version":"1.0"}}}' \
  "${response_dir}/initialize.json")"
tools_code="$(mcp_request \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  "${response_dir}/tools.json")"

if [[ "${initialize_code}" != "200" || "${tools_code}" != "200" ]]; then
  printf '[FAIL] MCP responses initialize=%s tools=%s\n' \
    "${initialize_code:-none}" "${tools_code:-none}" >&2
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  python3 - "${response_dir}" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
initialized = json.loads((root / "initialize.json").read_text(encoding="utf-8"))
tools = json.loads((root / "tools.json").read_text(encoding="utf-8"))
if initialized["result"]["serverInfo"]["name"] != "OligoSmith":
    raise SystemExit("unexpected MCP server name")
names = {item["name"] for item in tools["result"]["tools"]}
required = {
    "prepare_wf01_design_input",
    "download_himat_file",
    "create_wf01_batch",
    "get_wf01_batch",
    "revise_wf01_target_manifest",
    "submit_wf01_interaction_response",
    "run_wf01_until_blocked",
    "create_wf01_version",
    "get_wf01_report",
    "get_wf01_candidate",
    "get_wf01_export",
}
if names != required:
    raise SystemExit(
        f"unexpected MCP tool list: count={len(names)} "
        f"missing={sorted(required - names)} extra={sorted(names - required)}"
    )
print(f"[PASS] MCP initialize and focused tools/list; tool count={len(names)}")
PY
else
  printf '[PASS] MCP initialize and tools/list returned HTTP 200\n'
fi

agentd_compose ps
printf '[PASS] OligoSmith MCP deployment verified: %s/mcp\n' "${base_url}"
