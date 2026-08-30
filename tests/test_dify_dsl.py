from __future__ import annotations

from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DSL_PATH = PROJECT_ROOT / "deploy" / "dify" / "oligosmith-agentd-workflow.yml"

EXPECTED_OPERATIONS = {
    "WF01_PREFLIGHT",
    "WF01_CREATE",
    "WF01_STATUS",
    "WF01_REVISE_TARGETS",
    "WF01_CONFIRM",
    "WF01_RUN",
    "WF01_RUN_M2",
    "WF01_RUN_M3",
    "WF01_RUN_M4",
    "WF01_RUN_M5",
    "WF01_RUN_M6",
    "WF01_INTERACTIONS",
    "WF01_RESPOND",
    "WF01_REPORT",
    "WF01_VERSION",
    "WF01_EXPORT",
    "WF02_PREFLIGHT",
    "WF02_CREATE",
    "WF02_DRAFT_STATUS",
    "WF02_DRAFT_RESPOND",
    "WF02_STATUS",
    "WF02_RUN",
    "WF02_TARGET_RESPOND",
    "WF02_INTERNAL_RESPOND",
    "WF02_REPORT",
    "WF02_PANEL",
    "WF02_VERSION",
    "WF02_EXPORT",
}

EXPECTED_ROUTE_FRAGMENTS = {
    "/v1/wf01/tasks/preflight",
    "/v1/wf01/tasks",
    "/v1/wf01/tasks/{batch_id}",
    "/v1/wf01/tasks/{batch_id}/targets",
    "/v1/wf01/tasks/{batch_id}/confirm",
    "/v1/wf01/tasks/{batch_id}/run-until-blocked",
    "/v1/wf01/tasks/{batch_id}/run-m2",
    "/v1/wf01/tasks/{batch_id}/run-m3",
    "/v1/wf01/tasks/{batch_id}/run-m4",
    "/v1/wf01/tasks/{batch_id}/run-m5",
    "/v1/wf01/tasks/{batch_id}/run-m6",
    "/v1/wf01/tasks/{batch_id}/interactions",
    "/v1/wf01/tasks/{batch_id}/interactions/response",
    "/v1/wf01/tasks/{batch_id}/report",
    "/v1/wf01/tasks/{batch_id}/versions",
    "/v1/wf01/tasks/{batch_id}/exports/{format_name}",
    "/v1/wf02/tasks/preflight",
    "/v1/wf02/tasks",
    "/v1/wf02/drafts/{draft_id}",
    "/v1/wf02/drafts/{draft_id}/interactions/response",
    "/v1/wf02/tasks/{batch_id}",
    "/v1/wf02/tasks/{batch_id}/run-until-blocked",
    "/v1/wf02/tasks/{batch_id}/targets/{target_id}/interactions/response",
    "/v1/wf02/tasks/{batch_id}/internal-control/interactions/response",
    "/v1/wf02/tasks/{batch_id}/report",
    "/v1/wf02/tasks/{batch_id}/panels/{panel_id}",
    "/v1/wf02/tasks/{batch_id}/versions",
    "/v1/wf02/tasks/{batch_id}/exports/{format_name}",
}


def _dsl() -> dict:
    return yaml.safe_load(DSL_PATH.read_text(encoding="utf-8"))


def _nodes(dsl: dict) -> dict[str, dict]:
    return {node["id"]: node for node in dsl["workflow"]["graph"]["nodes"]}


def test_dify_dsl_is_importable_plugin_free_workflow() -> None:
    dsl = _dsl()

    assert dsl["kind"] == "app"
    assert dsl["version"] == "0.7.0"
    assert dsl["app"]["mode"] == "workflow"
    assert dsl["dependencies"] == []

    executable_types = {
        node["data"]["type"]
        for node in dsl["workflow"]["graph"]["nodes"]
        if node["type"] != "custom-note"
    }
    assert executable_types == {
        "start",
        "code",
        "if-else",
        "http-request",
        "variable-aggregator",
        "end",
    }

    raw = DSL_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "langgenius/",
        "plugin_unique_identifier",
        "FunctionCalling",
        '"type": "agent"',
        '"type": "llm"',
        "gpt-",
    ):
        assert forbidden not in raw


def test_dify_workflow_exposes_deterministic_wf01_wf02_route_map() -> None:
    dsl = _dsl()
    nodes = _nodes(dsl)

    start_variables = {
        variable["variable"]: variable
        for variable in nodes["start"]["data"]["variables"]
    }
    assert set(start_variables["operation"]["options"]) == EXPECTED_OPERATIONS
    assert {
        "payload_json",
        "batch_id",
        "draft_id",
        "target_id",
        "panel_id",
        "format_name",
    } <= start_variables.keys()

    dispatch = nodes["dispatch"]["data"]
    assert dispatch["code_language"] == "python3"
    for operation in EXPECTED_OPERATIONS:
        assert f'"{operation}"' in dispatch["code"]
    for route in EXPECTED_ROUTE_FRAGMENTS:
        assert f'"{route}"' in dispatch["code"]

    http_nodes = [
        node
        for node in nodes.values()
        if node["data"]["type"] == "http-request"
    ]
    assert {node["data"]["method"] for node in http_nodes} == {
        "get",
        "post",
        "patch",
    }
    assert all(
        node["data"]["url"] == "{{#dispatch.request_url#}}"
        for node in http_nodes
    )
    assert all(
        node["data"]["authorization"]
        == {
            "type": "api-key",
            "config": {
                "type": "bearer",
                "api_key": "{{#env.AGENTD_BEARER_TOKEN#}}",
            },
        }
        for node in http_nodes
    )


def test_dify_workflow_has_no_secrets_and_documents_validation_boundaries() -> None:
    dsl = _dsl()
    nodes = _nodes(dsl)
    environment = {
        variable["name"]: variable
        for variable in dsl["workflow"]["environment_variables"]
    }

    assert environment["AGENTD_BASE_URL"]["value_type"] == "string"
    assert environment["AGENTD_BEARER_TOKEN"]["value_type"] == "secret"
    assert environment["AGENTD_BEARER_TOKEN"]["value"] == ""

    raw = DSL_PATH.read_text(encoding="utf-8")
    assert "MCP_JWT_SECRET" not in raw
    assert "BEGIN PRIVATE KEY" not in raw

    note_text = "\n".join(
        node["data"].get("text", "")
        for node in nodes.values()
        if node["type"] == "custom-note"
    )
    assert "WF-01 单靶标流程" in note_text
    assert "WF-02 普通多重流程（N01–N32）" in note_text
    assert "P1–P6 是独立工具包" in note_text
    assert "P5 是多工具组合" in note_text
    assert "P6 证据归属" in note_text
    assert "0 候选不中断批次" in note_text
    assert "默认仪器" in note_text
    assert "报告单一来源" in note_text
    assert "外部 Step 5 / 后端 M4" in note_text
    assert "当前标准路线不包含外部 Step 4" in note_text
    assert "不要把 `RUN_M5` 标成外部 Step 5" in note_text
    assert "**M5** 特异性验证" not in note_text

    edge_pairs = {
        (edge["source"], edge["target"])
        for edge in dsl["workflow"]["graph"]["edges"]
    }
    assert ("start", "dispatch") in edge_pairs
    assert ("dispatch", "validation_gate") in edge_pairs
    assert ("status_aggregator", "end") in edge_pairs
    assert {
        ("http_get", "body_aggregator"),
        ("http_post", "body_aggregator"),
        ("http_patch", "body_aggregator"),
        ("validation_error", "body_aggregator"),
    } <= edge_pairs
