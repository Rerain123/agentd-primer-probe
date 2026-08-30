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

for scope in wf01 wf02 wf03 wf04 wf05; do
  scoped_unauthorized_code="$(
    curl --silent --show-error --connect-timeout 3 --max-time 15 \
      --request POST \
      --header 'Content-Type: application/json' \
      --header 'Accept: application/json, text/event-stream' \
      --data '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
      --output "${response_dir}/${scope}-unauthorized.json" \
      --write-out '%{http_code}' \
      "${base_url}/mcp/${scope}/" || true
  )"
  if [[ "${scoped_unauthorized_code}" != "401" ]]; then
    printf '[FAIL] /mcp/%s/ without JWT returned HTTP %s instead of 401\n' \
      "${scope}" "${scoped_unauthorized_code:-none}" >&2
    exit 1
  fi
done
printf '[PASS] workflow-scoped MCP endpoints reject missing JWT\n'

wf02_export_unauthorized_code="$(
  curl --silent --show-error --connect-timeout 3 --max-time 15 \
    --output "${response_dir}/wf02-export-unauthorized.json" \
    --write-out '%{http_code}' \
    "${base_url}/v1/wf02/tasks/00000000-0000-0000-0000-000000000001/exports/html" || true
)"
if [[ "${wf02_export_unauthorized_code}" != "401" ]]; then
  printf '[FAIL] WF-02 export without JWT returned HTTP %s instead of 401\n' \
    "${wf02_export_unauthorized_code:-none}" >&2
  exit 1
fi
printf '[PASS] WF-02 report downloads reject missing JWT\n'

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
  local endpoint="${3:-/mcp}"
  curl --silent --show-error --connect-timeout 3 --max-time 30 \
    --request POST \
    --header "Authorization: Bearer ${token}" \
    --header 'Content-Type: application/json' \
    --header 'Accept: application/json, text/event-stream' \
    --header 'MCP-Protocol-Version: 2025-11-25' \
    --data "${payload}" \
    --output "${output}" \
    --write-out '%{http_code}' \
    "${base_url}${endpoint}" || true
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

for scope in wf01 wf02 wf03 wf04 wf05; do
  scoped_initialize_code="$(mcp_request \
    '{"jsonrpc":"2.0","id":11,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"deploy-verify","version":"1.0"}}}' \
    "${response_dir}/${scope}-initialize.json" \
    "/mcp/${scope}/")"
  scoped_tools_code="$(mcp_request \
    '{"jsonrpc":"2.0","id":12,"method":"tools/list","params":{}}' \
    "${response_dir}/${scope}-tools.json" \
    "/mcp/${scope}/")"
  if [[ "${scoped_initialize_code}" != "200" || "${scoped_tools_code}" != "200" ]]; then
    printf '[FAIL] scoped MCP %s responses initialize=%s tools=%s\n' \
      "${scope}" "${scoped_initialize_code:-none}" "${scoped_tools_code:-none}" >&2
    exit 1
  fi
done

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


def contains_chinese(text):
    return any("\u4e00" <= character <= "\u9fff" for character in text)


def schema_contains_title(value):
    if isinstance(value, dict):
        return "title" in value or any(schema_contains_title(child) for child in value.values())
    if isinstance(value, list):
        return any(schema_contains_title(child) for child in value)
    return False


def verify_chinese_metadata(tool_items, label):
    for tool in tool_items:
        tool_name = tool.get("name")
        if not contains_chinese(tool.get("description") or ""):
            raise SystemExit(f"MCP tool description is not Chinese: {label}.{tool_name}")
        schema = tool.get("inputSchema") or {}
        if schema_contains_title(schema):
            raise SystemExit(f"MCP schema contains generated display title: {label}.{tool_name}")
        for parameter_name, parameter in (schema.get("properties") or {}).items():
            if not contains_chinese(parameter.get("description") or ""):
                raise SystemExit(
                    f"MCP parameter description is not Chinese: "
                    f"{label}.{tool_name}.{parameter_name}"
                )


def verify_terminal_output_schema(tool, label):
    schema = tool.get("outputSchema") or {}
    branches = schema.get("oneOf") or []
    if len(branches) != 2:
        raise SystemExit(
            f"MCP terminal outputSchema is missing: {label}.{tool['name']}"
        )
    card = branches[0]
    if card.get("additionalProperties") is not False:
        raise SystemExit(
            f"MCP terminal outputSchema allows extra fields: {label}.{tool['name']}"
        )
    if set(card.get("required") or []) != {"data", "type"}:
        raise SystemExit(
            f"MCP terminal outputSchema has wrong required fields: "
            f"{label}.{tool['name']}"
        )
    card_type = (card.get("properties") or {}).get("type") or {}
    if card_type.get("const") != "primer_design_output":
        raise SystemExit(
            f"MCP terminal outputSchema has wrong discriminator: "
            f"{label}.{tool['name']}"
        )


legacy_tool_items = tools["result"]["tools"]
verify_chinese_metadata(legacy_tool_items, "legacy")
names = {item["name"] for item in legacy_tool_items}
required = {
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
    "create_wf02_batch",
    "submit_wf02_input_response",
    "get_wf02_batch",
    "run_wf02_until_blocked",
    "submit_wf02_child_response",
    "submit_wf02_internal_control_response",
    "create_wf02_version",
    "get_wf02_report",
    "get_wf02_panel",
    "get_wf02_export",
    "run_wf03_internal_control",
    "retry_wf03_internal_control",
    "get_wf03_report",
    "run_wf04_validation",
    "retry_wf04_validation",
    "get_wf04_report",
    "run_wf05_variation",
    "retry_wf05_variation",
    "get_wf05_report",
}
terminal_capable = {
    "create_wf01_batch",
    "create_wf02_batch",
    "get_wf01_batch",
    "get_wf01_export",
    "get_wf02_batch",
    "get_wf02_export",
    "get_wf03_report",
    "get_wf04_report",
    "get_wf05_report",
    "retry_wf03_internal_control",
    "retry_wf04_validation",
    "retry_wf05_variation",
    "revise_wf01_target_manifest",
    "run_wf01_until_blocked",
    "run_wf02_until_blocked",
    "run_wf03_internal_control",
    "run_wf04_validation",
    "run_wf05_variation",
    "submit_wf01_interaction_response",
    "submit_wf02_child_response",
    "submit_wf02_input_response",
    "submit_wf02_internal_control_response",
}
if names != required:
    raise SystemExit(
        f"unexpected MCP tool list: count={len(names)} "
        f"missing={sorted(required - names)} extra={sorted(names - required)}"
    )
for tool in legacy_tool_items:
    if tool["name"] in terminal_capable:
        verify_terminal_output_schema(tool, "legacy")
wf03 = next(
    item
    for item in tools["result"]["tools"]
    if item["name"] == "run_wf03_internal_control"
)
wf03_properties = wf03.get("inputSchema", {}).get("properties", {})
if "input_artifact_id" not in wf03_properties:
    raise SystemExit("WF-03 tool schema is stale: missing=input_artifact_id")
for phrase in (
    "WF-03 新任务唯一入口",
    "必须先调用 download_himat_file",
    "顶层input_artifact_id原样传入",
    "禁止跳过下载",
    "必须立即调用",
    "禁止在调用前分析",
    "只能询问该字段并停止",
):
    if phrase not in wf03.get("description", ""):
        raise SystemExit(f"WF-03 tool routing contract is stale: missing={phrase}")
download = next(
    item
    for item in tools["result"]["tools"]
    if item["name"] == "download_himat_file"
)
download_description = download.get("description", "")
if "WF-01/WF-03/WF-04/WF-05直接复用" not in download_description:
    raise SystemExit("WF-03 download routing contract is stale")
if "downstream.wf03" in download_description or "downstream.wf04" in download_description:
    raise SystemExit("WF-03 download routing contract is stale")
wf04 = next(
    item
    for item in tools["result"]["tools"]
    if item["name"] == "run_wf04_validation"
)
wf04_properties = wf04.get("inputSchema", {}).get("properties", {})
if "input_artifact_id" not in wf04_properties:
    raise SystemExit("WF-04 tool schema is stale: missing=input_artifact_id")
for phrase in (
    "WF-04新任务唯一入口",
    "download_himat_file",
    "顶层input_artifact_id原样传入",
    "禁止调用前分析",
    "primer_design_output",
):
    if phrase not in wf04.get("description", ""):
        raise SystemExit(f"WF-04 tool routing contract is stale: missing={phrase}")
if "顶层input_artifact_id" not in download_description:
    raise SystemExit("WF-04 download routing contract is stale")
wf05 = next(
    item
    for item in tools["result"]["tools"]
    if item["name"] == "run_wf05_variation"
)
wf05_schema = wf05.get("inputSchema", {})
wf05_properties = wf05_schema.get("properties", {})
if set(wf05_schema.get("required", [])) != {"forward_primer", "reverse_primer"}:
    raise SystemExit("WF-05 tool schema is stale: required fields")
if not {"input_artifact_id", "sequences"}.issubset(wf05_properties):
    raise SystemExit("WF-05 tool schema is stale: sequence set inputs")
for phrase in (
    "WF-05新任务唯一入口",
    "download_himat_file",
    "input_artifact_id原样传入",
    "禁止模型重算",
    "禁止模型重算或自动调用其他工作流",
):
    if phrase not in wf05.get("description", ""):
        raise SystemExit(f"WF-05 tool routing contract is stale: missing={phrase}")
if "WF-05直接复用" not in download_description:
    raise SystemExit("WF-05 download routing contract is stale")

scoped_required = {
    "wf01": {
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
    },
    "wf02": {
        "download_himat_file",
        "create_wf02_batch",
        "submit_wf02_input_response",
        "get_wf02_batch",
        "run_wf02_until_blocked",
        "submit_wf02_child_response",
        "submit_wf02_internal_control_response",
        "create_wf02_version",
        "get_wf02_report",
        "get_wf02_panel",
        "get_wf02_export",
    },
    "wf03": {
        "download_himat_file",
        "run_wf03_internal_control",
        "retry_wf03_internal_control",
        "get_wf03_report",
    },
    "wf04": {
        "download_himat_file",
        "run_wf04_validation",
        "retry_wf04_validation",
        "get_wf04_report",
    },
    "wf05": {
        "download_himat_file",
        "run_wf05_variation",
        "retry_wf05_variation",
        "get_wf05_report",
    },
}
for scope, expected in scoped_required.items():
    scoped_initialized = json.loads(
        (root / f"{scope}-initialize.json").read_text(encoding="utf-8")
    )
    scoped_tools = json.loads(
        (root / f"{scope}-tools.json").read_text(encoding="utf-8")
    )
    workflow = scope.upper().replace("WF", "WF-")
    if scoped_initialized["result"]["serverInfo"]["name"] != f"OligoSmith {workflow}":
        raise SystemExit(f"unexpected scoped MCP server name: {scope}")
    scoped_instructions = scoped_initialized["result"].get("instructions", "")
    if len(scoped_instructions) >= 260:
        raise SystemExit(f"scoped MCP instructions are too long: {scope}")
    for required_instruction in (
        "终态primer_design_output已由工具直接交付",
        "不得生成助手文本",
        "不得再次调用工具",
    ):
        if required_instruction not in scoped_instructions:
            raise SystemExit(
                f"scoped MCP missing HiMat terminal projection rule: {scope}"
            )
    scoped_names = {
        item["name"] for item in scoped_tools["result"]["tools"]
    }
    if scoped_names != expected:
        raise SystemExit(
            f"unexpected scoped MCP tool list: scope={scope} "
            f"missing={sorted(expected - scoped_names)} "
            f"extra={sorted(scoped_names - expected)}"
        )
    scoped_tool_items = scoped_tools["result"]["tools"]
    verify_chinese_metadata(scoped_tool_items, scope)
    for tool in scoped_tool_items:
        description = tool.get("description", "")
        if "终态输出结构由outputSchema定义" in description:
            raise SystemExit(
                f"scoped MCP repeats terminal schema description: {scope}.{tool['name']}"
            )
        if tool["name"] in terminal_capable:
            verify_terminal_output_schema(tool, scope)
print(
    f"[PASS] legacy and workflow-scoped MCP catalogs verified; "
    f"legacy tool count={len(names)}"
)
PY
else
  printf '[PASS] MCP initialize and tools/list returned HTTP 200\n'
fi

agentd_compose ps
printf '[PASS] OligoSmith MCP deployment verified: %s/mcp and scoped endpoints\n' "${base_url}"
