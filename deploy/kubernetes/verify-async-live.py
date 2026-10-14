"""Read-only authenticated live MCP/REST checks. Never print credentials or biology."""
import hashlib
import json
import time
import urllib.request
from pathlib import Path

import jwt
from agentd.auth import JWTSettings
from agentd.config import load_config

settings = JWTSettings.from_env()
claims = {"sub": "oligosmith-async-release-check", "exp": int(time.time()) + 120}
if settings.issuer:
    claims["iss"] = settings.issuer
if settings.audience:
    claims["aud"] = settings.audience
token = jwt.encode(claims, settings.secret, algorithm=settings.algorithm)
headers = {"Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def request(path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    started = time.monotonic()
    with urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8000" + path, data=data, headers=headers), timeout=15) as response:
        value = json.load(response)
    return value, round(time.monotonic() - started, 4)


result = {"scope": "read_only_live_interfaces", "status": "PASS", "queries": {}}
for path in ("/health", "/ready"):
    _, seconds = request(path)
    result[path] = {"http_status": 200, "seconds": seconds}
initialized, _ = request("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "release-check", "version": "1"}}})
instructions = initialized["result"].get("instructions", "")
assert "查询进度" in instructions and "无需保持连接" in instructions
result["mcp_async_instructions"] = True
tools, _ = request("/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
names = {tool["name"] for tool in tools["result"]["tools"]}
assert {"create_wf01_batch", "get_wf01_batch", "create_wf02_batch", "get_wf02_batch"} <= names
result["mcp_submit_and_query_tools_present"] = True

for workflow, folder in (("wf01", "wf01-v02-batches"), ("wf02", "wf02-v01-batches")):
    paths = sorted((Path("/data/jobs") / folder).glob("*.json"))
    assert paths, "No persisted workflow evidence available"
    path = paths[-1]
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    batch_id = json.loads(path.read_text())["batch_id"]
    view, seconds = request(f"/v1/{workflow}/tasks/{batch_id}/progress")
    wire, mcp_seconds = request("/mcp", {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": f"get_{workflow}_batch", "arguments": {"batch_id": batch_id}}})
    assert not wire["result"].get("isError", False), "MCP query failed"
    assert before == hashlib.sha256(path.read_bytes()).hexdigest(), "Query modified scientific state"
    result["queries"][workflow] = {"rest_seconds": seconds, "mcp_seconds": mcp_seconds, "scientific_state_unchanged": True, "status": view.get("status"), "background_state": view.get("background_execution", {}).get("state")}
config = load_config()
result["blast_runtime"] = config["tools"]["blast_runtime"]
result["blast_cache_enabled"] = config["tools"]["blast_cache"]["enabled"]
assert result["blast_runtime"]["timeout_seconds"] is None
assert result["blast_runtime"]["num_threads"] == 16
print(json.dumps(result, ensure_ascii=True), flush=True)
