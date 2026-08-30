"""WF-01 conversation-to-tool input adapter for the MCP boundary.

The deterministic M1 service intentionally accepts only its canonical batch
shape.  Conversation agents, however, receive a frontend
``primer_design_input`` message whose camelCase fields are deliberately UI
oriented.  This module is the single adapter between those two contracts.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from .errors import InputValidationError


DESIGN_INPUT_TYPE = "primer_design_input"
DESIGN_UPDATE_TYPE = "primer_design_input_update"
WAITING_STATUS = "WAITING_USER"
DESIGN_INPUT_ID_PATTERN = re.compile(r"^design-input-[a-f0-9]{32}$")
INPUT_ARTIFACT_ID_PATTERN = re.compile(r"^himat-[a-f0-9]{32}$")


def _nonempty_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _validation_error(message: str, *, fields: list[str]) -> InputValidationError:
    return InputValidationError(
        message,
        details={
            "assistant_message_type": DESIGN_INPUT_TYPE,
            "user_message_type": DESIGN_UPDATE_TYPE,
            "invalid_or_missing_fields": fields,
            "retry_in_current_turn": True,
            "model_must_not_rebuild_messages": True,
            "reuse_current_context_only": True,
            "maximum_mapping_retries": 1,
            "recovery_action": "REUSE_CURRENT_STRUCTURED_MESSAGES_ONCE",
            "retry_policy": (
                "从当前上下文复用最近一次原始 primer_design_input 和当前 "
                "primer_design_input_update，修正工具参数后重试一次；不要改写对象或要求用户再次确认。"
                "若当前上下文确实没有任一结构化对象，再结束当前轮次并请求宿主转发。"
            ),
        },
    )


def validate_design_input_form(message: Any) -> dict[str, Any]:
    """Validate the immutable assistant form stored in conversation history."""
    if not isinstance(message, dict):
        raise _validation_error(
            "WF-01 预检缺少智能体先前返回的设计输入表单",
            fields=["design_input_form"],
        )

    data = message.get("data")
    if message.get("type") != DESIGN_INPUT_TYPE or not isinstance(data, dict):
        raise _validation_error(
            "design_input_form 不是 primer_design_input 对象",
            fields=["type", "data"],
        )

    invalid: list[str] = []
    # 0.3.21 and earlier persisted flow-control metadata in the public form.
    # New forms deliberately omit it, but accepting the legacy fields here
    # lets an already-open form finish after a rolling deployment.
    flow_control = message.get("flowControl")
    if flow_control is not None:
        if not isinstance(flow_control, dict):
            invalid.append("flowControl")
            flow_control = {}
        if flow_control.get("action") != "WAIT_FOR_USER":
            invalid.append("flowControl.action")
        if flow_control.get("stopCurrentTurn") is not True:
            invalid.append("flowControl.stopCurrentTurn")
        if flow_control.get("resumeMessageType") != DESIGN_UPDATE_TYPE:
            invalid.append("flowControl.resumeMessageType")
    interaction_id = message.get("interactionId")
    if interaction_id is not None:
        if not isinstance(interaction_id, str) or not DESIGN_INPUT_ID_PATTERN.fullmatch(
            interaction_id
        ):
            invalid.append("interactionId")
        if not isinstance(flow_control, dict):
            invalid.append("flowControl")
            flow_control = {}
        if flow_control.get("deduplicationKey") != interaction_id:
            invalid.append("flowControl.deduplicationKey")
        if flow_control.get("onDuplicate") != "KEEP_EXISTING":
            invalid.append("flowControl.onDuplicate")
    if data.get("status") != WAITING_STATUS:
        invalid.append("data.status")
    # Some HiMat form-submit paths mirror the user's click into the stored form
    # before forwarding it. Confirmation is still proven by the separate
    # primer_design_input_update, so either boolean form snapshot is safe here.
    if not isinstance(data.get("confirmed"), bool):
        invalid.append("data.confirmed")
    if not isinstance(data.get("canStart"), bool):
        invalid.append("data.canStart")

    source_file = data.get("sourceFile")
    if not isinstance(source_file, dict):
        invalid.append("data.sourceFile")
        source_file = {}
    if not _nonempty_text(source_file.get("fileName")):
        invalid.append("data.sourceFile.fileName")
    if _nonempty_text(source_file.get("fileType")).upper() != "FASTA":
        invalid.append("data.sourceFile.fileType")
    input_artifact_id = source_file.get("inputArtifactId")
    if input_artifact_id is not None and (
        not isinstance(input_artifact_id, str)
        or not INPUT_ARTIFACT_ID_PATTERN.fullmatch(input_artifact_id)
    ):
        invalid.append("data.sourceFile.inputArtifactId")
    input_mode = source_file.get("inputMode")
    if input_mode is not None and input_mode not in {
        "UPLOADED_FILE",
        "INLINE_SEQUENCE",
    }:
        invalid.append("data.sourceFile.inputMode")
    if input_mode == "INLINE_SEQUENCE" and input_artifact_id is None:
        invalid.append("data.sourceFile.inputArtifactId")

    design_input = data.get("designInput")
    if not isinstance(design_input, dict):
        invalid.append("data.designInput")
        design_input = {}
    for field in ("targetOrganism", "host", "coverageRequirement"):
        if not isinstance(design_input.get(field), str):
            invalid.append(f"data.designInput.{field}")

    if invalid:
        raise _validation_error(
            "智能体返回的引物设计输入表单格式不正确",
            fields=invalid,
        )
    return copy.deepcopy(data)


def extract_user_field_updates(message: Any) -> dict[str, str]:
    """Read only editable field values from the subsequent user message.

    The formal wire shape is ``primer_design_input_update``. Direct
    ``designInput``, ``changedFields`` and direct field dictionaries remain
    accepted at the MCP boundary so the agent does not start guessing names if
    a host backend uses a thinner form-submit envelope. A formal update may
    contain an empty ``designInput`` object when the user confirms the original
    form without changing any fields.
    """
    if not isinstance(message, dict):
        raise _validation_error(
            "WF-01 预检缺少用户提交的字段信息",
            fields=["user_field_updates"],
        )
    formal_update = message.get("type") == DESIGN_UPDATE_TYPE
    container: Any = message.get("data") if formal_update else message
    if formal_update and not isinstance(container, dict):
        raise _validation_error(
            "primer_design_input_update.data 必须为对象",
            fields=["user_field_updates.data"],
        )

    explicit_field_container = False
    if isinstance(container, dict):
        if "designInput" in container:
            container = container.get("designInput")
            explicit_field_container = True
        elif "changedFields" in container:
            container = container.get("changedFields")
            explicit_field_container = True
    if not isinstance(container, dict):
        raise _validation_error(
            "用户字段信息必须为对象",
            fields=["user_field_updates.data.designInput"],
        )

    allowed = ("targetOrganism", "host", "coverageRequirement")
    updates = {
        field: value.strip()
        for field in allowed
        if isinstance((value := container.get(field)), str)
    }
    unchanged_form_confirmation = formal_update and not container
    if not updates and not unchanged_form_confirmation:
        raise _validation_error(
            "用户消息中没有可识别的设计字段",
            fields=[f"user_field_updates.{field}" for field in allowed],
        )
    return updates


def _canonical_object(value: Any) -> Any:
    """Convert an M1 result-style object back to an accepted request object."""
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    if any(value.get(key) not in (None, "") for key in ("name", "scientific_name", "common_name", "taxid", "accession", "record_id")):
        return copy.deepcopy(value)
    identifier_type = _nonempty_text(value.get("identifier_type")).upper()
    identifier = _nonempty_text(value.get("identifier"))
    if identifier_type == "TAXID" and identifier:
        return {"taxid": identifier}
    if identifier_type == "ACCESSION" and identifier:
        return {"accession": identifier}
    name = _nonempty_text(value.get("normalized_name")) or identifier
    return {"name": name} if name else copy.deepcopy(value)


def _first_present(container: dict[str, Any], names: tuple[str, ...]) -> tuple[str | None, Any]:
    for name in names:
        if container.get(name) not in (None, "", []):
            return name, container[name]
    return None, None


def normalize_wf01_mcp_payload(
    payload: Any,
    design_input_form: Any = None,
    user_field_updates: Any = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge the assistant form and user field update, then map to M1.

    User-updated values override the assistant's prefilled values; omitted
    fields inherit those prefills. The server-side artifact layer materializes
    FASTA content into ``payload``; neither conversation message contains it.
    """
    if not isinstance(payload, dict):
        raise InputValidationError(
            "WF-01 payload 必须为对象",
            details={"received_type": type(payload).__name__},
        )
    if design_input_form is None and user_field_updates is None:
        normalized = copy.deepcopy(payload)
        defaults = normalized.get("batch_defaults")
        defaults = copy.deepcopy(defaults) if isinstance(defaults, dict) else {}
        design_input = normalized.pop("designInput", None) or normalized.pop(
            "design_input", None
        )
        if isinstance(design_input, dict):
            target = _nonempty_text(design_input.get("targetOrganism"))
            host = _nonempty_text(design_input.get("host"))
            coverage = _nonempty_text(design_input.get("coverageRequirement"))
            if target:
                defaults["target_organism"] = {"name": target}
            if host:
                defaults["hosts"] = [{"name": host}]
            if coverage:
                defaults["coverage_requirement"] = coverage

        normalized.pop("sourceFile", None)
        normalized.pop("source_file", None)

        target_alias, target_value = _first_present(
            normalized,
            ("target_organism", "target_species", "target_object"),
        )
        if target_alias:
            if defaults.get("target_organism") in (None, "", {}):
                defaults["target_organism"] = _canonical_object(target_value)
            normalized.pop(target_alias, None)
        host_alias, host_value = _first_present(
            normalized,
            ("hosts", "host", "host_species", "host_objects"),
        )
        if host_alias:
            if defaults.get("hosts") in (None, "", []):
                host_values = host_value if isinstance(host_value, list) else [host_value]
                defaults["hosts"] = [_canonical_object(item) for item in host_values]
            normalized.pop(host_alias, None)
        coverage = normalized.pop("coverage_requirement", None)
        if coverage not in (None, "") and defaults.get(
            "coverage_requirement"
        ) in (None, ""):
            defaults["coverage_requirement"] = coverage
        if defaults:
            normalized["batch_defaults"] = defaults
        return normalized, {
            "input_mode": "DIRECT_MODEL_INDEPENDENT_FALLBACK",
            "assistant_message_type": DESIGN_INPUT_TYPE,
            "user_message_type": DESIGN_UPDATE_TYPE,
            "user_updated_fields": [],
            "do_not_retry_with_alternate_field_names": True,
        }
    if design_input_form is None or user_field_updates is None:
        raise _validation_error(
            "设计输入表单和用户字段更新必须同时提供，或同时省略以使用直接输入",
            fields=["design_input_form", "user_field_updates"],
        )

    form_interaction_id = _nonempty_text(
        design_input_form.get("interactionId")
        if isinstance(design_input_form, dict)
        else None
    )
    update_interaction_id = _nonempty_text(
        user_field_updates.get("interactionId")
        if isinstance(user_field_updates, dict)
        else None
    )
    if (
        form_interaction_id
        and update_interaction_id
        and form_interaction_id != update_interaction_id
    ):
        raise _validation_error(
            "用户提交的设计输入 interactionId 与当前表单不一致",
            fields=[
                "design_input_form.interactionId",
                "user_field_updates.interactionId",
            ],
        )

    form = validate_design_input_form(design_input_form)
    updates = extract_user_field_updates(user_field_updates)
    design_input = copy.deepcopy(form["designInput"])
    design_input.update(updates)
    normalized = copy.deepcopy(payload)
    mapped_aliases: list[str] = []

    # Remove UI-only envelopes if an agent copied them into the scientific
    # payload. The separately validated conversation messages are authoritative.
    for key in (
        "primer_design_input",
        "primer_design_input_update",
        "confirmed_design_input",
        "design_input_form",
        "user_field_updates",
        "designInput",
        "design_input",
    ):
        if key in normalized:
            normalized.pop(key)
            mapped_aliases.append(key)
    if normalized.get("type") == DESIGN_INPUT_TYPE:
        normalized.pop("type", None)
        normalized.pop("data", None)
        mapped_aliases.append("type/data")

    defaults = normalized.get("batch_defaults")
    if not isinstance(defaults, dict):
        defaults = {}
    else:
        defaults = copy.deepcopy(defaults)

    # Accept the field names repeatedly tried by general-purpose agents.  They
    # are fallback inputs only; the merged conversation values win below.
    target_alias, target_value = _first_present(
        normalized,
        ("target_organism", "target_species", "target_object"),
    )
    if target_alias:
        defaults["target_organism"] = _canonical_object(target_value)
        normalized.pop(target_alias, None)
        mapped_aliases.append(target_alias)

    host_alias, host_value = _first_present(
        normalized,
        ("hosts", "host", "host_species", "host_objects"),
    )
    if host_alias:
        host_values = host_value if isinstance(host_value, list) else [host_value]
        defaults["hosts"] = [_canonical_object(item) for item in host_values]
        normalized.pop(host_alias, None)
        mapped_aliases.append(host_alias)

    target_organism = _nonempty_text(design_input.get("targetOrganism"))
    host = _nonempty_text(design_input.get("host"))
    coverage = _nonempty_text(design_input.get("coverageRequirement"))
    if target_organism:
        defaults["target_organism"] = {"name": target_organism}
    else:
        defaults.pop("target_organism", None)
    if host:
        defaults["hosts"] = [{"name": host}]
    else:
        defaults.pop("hosts", None)
    if coverage:
        defaults["coverage_requirement"] = coverage
    else:
        defaults.pop("coverage_requirement", None)
    if defaults:
        normalized["batch_defaults"] = defaults
    else:
        normalized.pop("batch_defaults", None)

    source_file = form["sourceFile"]
    file_name = _nonempty_text(source_file["fileName"])
    if normalized.get("fasta_text") is not None and not normalized.get("sequence_files"):
        normalized["sequence_files"] = [
            {"file_name": file_name, "fasta_text": normalized.pop("fasta_text")}
        ]
        mapped_aliases.append("fasta_text")
    elif isinstance(normalized.get("sequence_files"), list):
        for index, item in enumerate(normalized["sequence_files"]):
            if isinstance(item, dict) and not _nonempty_text(item.get("file_name") or item.get("name")):
                item["file_name"] = file_name if index == 0 else f"{index + 1}-{file_name}"

    has_template = any(
        normalized.get(field) not in (None, "", [])
        for field in (
            "input_artifact_id",
            "sequence_files",
            "template_records",
            "sequences",
            "sequence",
            "fasta_text",
        )
    )
    form_artifact_id = _nonempty_text(source_file.get("inputArtifactId"))
    payload_artifact_id = _nonempty_text(normalized.get("input_artifact_id"))
    if form_artifact_id and payload_artifact_id and (
        form_artifact_id != payload_artifact_id
    ):
        raise _validation_error(
            "payload 的输入引用与当前设计表单不一致",
            fields=[
                "payload.input_artifact_id",
                "design_input_form.data.sourceFile.inputArtifactId",
            ],
        )
    if form_artifact_id and not has_template:
        normalized["input_artifact_id"] = form_artifact_id
        mapped_aliases.append("sourceFile.inputArtifactId")
        has_template = True
    if not has_template:
        raise InputValidationError(
            "已确认设计信息，但没有可用的上传文件引用或直接序列输入",
            details={
                "source_file": source_file,
                "required_payload_fields": [
                    "input_artifact_id",
                    "sequence_files[].fasta_text",
                    "fasta_text",
                    "template_records[].sequence",
                    "sequence",
                ],
                "retry_policy": (
                    "上传文件时用 download_himat_file 将 file_id 转换为 input_artifact_id；"
                    "直接粘贴序列时必须使用 prepare_wf01_design_input 返回的原始表单，"
                    "不得猜测、重建或再次转述序列。"
                ),
            },
        )

    mapping = {
        "input_mode": "FORM_PLUS_USER_UPDATE",
        "assistant_message_type": DESIGN_INPUT_TYPE,
        "user_message_type": DESIGN_UPDATE_TYPE,
        "design_input_interaction_id": form_interaction_id or None,
        "user_updated_fields": sorted(updates),
        "field_sources": {
            field: (
                "user_field_updates"
                if field in updates
                else "design_input_form"
            )
            for field in (
                "targetOrganism",
                "host",
                "coverageRequirement",
            )
        },
        "effective_design_input": copy.deepcopy(design_input),
        "specificity_context_complete": bool(target_organism),
        "missing_specificity_fields": [
            field
            for field, value in (
                ("targetOrganism", target_organism),
            )
            if not value
        ],
        "authoritative_mapping": {
            "merged.designInput.targetOrganism": "batch_defaults.target_organism.name",
            "merged.designInput.host": "batch_defaults.hosts[0].name",
            "merged.designInput.coverageRequirement": "batch_defaults.coverage_requirement",
            "assistant.data.sourceFile.fileName": "sequence_files[0].file_name",
            "assistant.data.sourceFile.inputArtifactId": "input_artifact_id",
        },
        "mapped_aliases": sorted(set(mapped_aliases)),
        "do_not_retry_with_alternate_field_names": True,
    }
    return normalized, mapping
