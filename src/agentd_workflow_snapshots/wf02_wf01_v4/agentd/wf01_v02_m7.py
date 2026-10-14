"""WF-01 PRD-v0.2 M7 versioned redesign helpers.

M7 never changes a source task.  It turns an explicit per-target change set
into a new batch input, computes an auditable field diff, and identifies the
targets whose M2--M5 results may safely be reused.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .errors import InputValidationError


M7_ENGINE_VERSION = "wf01-v0.2-m7.1"
M7_REDESIGN_CONTRACT_VERSION = "wf01-prd-v0.2-redesign-v1"
TARGET_CHANGE_FIELDS = {
    "target_id",
    "template",
    "target_organism",
    "hosts",
    "related_species",
    "background_organisms",
    "design_parameters",
}
TEMPLATE_CHANGE_FIELDS = {
    "record_name",
    "description",
    "sequence",
    "source_file",
}


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def stable_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _source_object(value: Any) -> Any:
    if not isinstance(value, dict):
        return None
    original = value.get("original_input")
    return copy.deepcopy(original if original is not None else value)


def _source_objects(values: Any) -> list[Any]:
    if not isinstance(values, list):
        return []
    return [_source_object(value) for value in values if isinstance(value, dict)]


def target_input_model(target: dict[str, Any]) -> dict[str, Any]:
    """Extract only M1-relevant fields from a persisted target."""
    template = target.get("template") or {}
    parameters = target.get("parameters") or {}
    return {
        "template": {
            "record_name": template.get("record_name"),
            "description": template.get("description") or "",
            "sequence": template.get("sequence") or "",
            "source_file": template.get("source_file") or "inline",
        },
        "target_organism": _source_object(target.get("target_object")),
        "hosts": _source_objects(target.get("host_objects")),
        "related_species": _source_objects(target.get("related_objects")),
        "background_organisms": _source_objects(
            target.get("background_objects")
        ),
        "design_parameters": copy.deepcopy(
            parameters.get("user_overrides") or {}
        ),
    }


def _sequence_digest(value: Any) -> dict[str, Any]:
    sequence = str(value or "")
    return {
        "length_nt": len(sequence),
        "sha256": hashlib.sha256(sequence.encode("utf-8")).hexdigest(),
    }


def target_input_diff(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a compact field diff; template bases are represented by hashes."""
    changes: list[dict[str, Any]] = []
    before_template = before["template"]
    after_template = after["template"]
    for name in ("record_name", "description", "source_file"):
        if before_template.get(name) != after_template.get(name):
            changes.append(
                {
                    "path": f"template.{name}",
                    "before": copy.deepcopy(before_template.get(name)),
                    "after": copy.deepcopy(after_template.get(name)),
                }
            )
    if before_template.get("sequence") != after_template.get("sequence"):
        changes.append(
            {
                "path": "template.sequence",
                "before": _sequence_digest(before_template.get("sequence")),
                "after": _sequence_digest(after_template.get("sequence")),
            }
        )

    for name in (
        "target_organism",
        "hosts",
        "related_species",
        "background_organisms",
    ):
        if _canonical(before.get(name)) != _canonical(after.get(name)):
            changes.append(
                {
                    "path": name,
                    "before": copy.deepcopy(before.get(name)),
                    "after": copy.deepcopy(after.get(name)),
                }
            )

    before_parameters = before.get("design_parameters") or {}
    after_parameters = after.get("design_parameters") or {}
    for name in sorted(set(before_parameters) | set(after_parameters)):
        if before_parameters.get(name) != after_parameters.get(name):
            changes.append(
                {
                    "path": f"design_parameters.{name}",
                    "before": before_parameters.get(name),
                    "after": after_parameters.get(name),
                }
            )
    return changes


def _validate_change(change: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(change, dict):
        raise InputValidationError("每个靶标变更必须为对象")
    unknown = sorted(set(change) - TARGET_CHANGE_FIELDS)
    if unknown:
        raise InputValidationError(
            "M7 包含不支持的靶标变更字段",
            details={"unknown_fields": unknown},
        )
    target_id = change.get("target_id")
    if not isinstance(target_id, str) or not target_id.strip():
        raise InputValidationError("M7 靶标变更必须包含 target_id")
    if set(change) == {"target_id"}:
        raise InputValidationError(
            "M7 靶标变更必须至少修改一个输入字段",
            details={"target_id": target_id},
        )
    template = change.get("template")
    if template is not None:
        if not isinstance(template, dict):
            raise InputValidationError(
                "M7 template 变更必须为对象",
                details={"target_id": target_id},
            )
        unknown_template = sorted(set(template) - TEMPLATE_CHANGE_FIELDS)
        if unknown_template:
            raise InputValidationError(
                "M7 template 包含不支持字段",
                details={
                    "target_id": target_id,
                    "unknown_fields": unknown_template,
                },
            )
    return target_id, copy.deepcopy(change)


def apply_target_changes(
    source_targets: list[dict[str, Any]],
    target_changes: Any,
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    """Apply an explicit M7 change set and return inputs, reruns and diffs."""
    if not isinstance(target_changes, list) or not target_changes:
        raise InputValidationError("M7 target_changes 必须为非空数组")
    source_by_id = {
        str(target.get("target_id")): target for target in source_targets
    }
    if len(source_by_id) != len(source_targets) or "" in source_by_id:
        raise InputValidationError("源批次靶标 ID 不完整，不能安全创建 M7 版本")

    updates: dict[str, dict[str, Any]] = {}
    for raw_change in target_changes:
        target_id, change = _validate_change(raw_change)
        if target_id in updates:
            raise InputValidationError(
                "同一靶标只能提交一次 M7 变更",
                details={"target_id": target_id},
            )
        if target_id not in source_by_id:
            raise InputValidationError(
                "M7 变更引用了源批次中不存在的靶标",
                details={"target_id": target_id},
            )
        updates[target_id] = change

    revised_models: list[dict[str, Any]] = []
    rerun_target_ids: list[str] = []
    version_diff: list[dict[str, Any]] = []
    for target in source_targets:
        target_id = str(target["target_id"])
        before = target_input_model(target)
        after = copy.deepcopy(before)
        change = updates.get(target_id)
        if change:
            if "template" in change:
                after["template"].update(copy.deepcopy(change["template"]))
            for name in (
                "target_organism",
                "hosts",
                "related_species",
                "background_organisms",
                "design_parameters",
            ):
                if name in change:
                    after[name] = copy.deepcopy(change[name])
        fields = target_input_diff(before, after)
        if fields:
            rerun_target_ids.append(target_id)
            version_diff.append(
                {
                    "target_id": target_id,
                    "before_input_sha256": stable_sha256(before),
                    "after_input_sha256": stable_sha256(after),
                    "fields": fields,
                }
            )
        revised_models.append(after)
    if not rerun_target_ids:
        raise InputValidationError(
            "M7 未产生有效输入差异；不会创建空的新版本",
            details={"target_ids": sorted(updates)},
        )
    return revised_models, rerun_target_ids, version_diff


def preflight_payload_from_models(
    models: list[dict[str, Any]],
    *,
    task_name: str,
) -> dict[str, Any]:
    """Build a full M1-shaped payload so all targets are revalidated together."""
    records: list[dict[str, Any]] = []
    overrides: dict[str, dict[str, Any]] = {}
    for model in models:
        template = model["template"]
        record_name = str(template.get("record_name") or "")
        records.append(copy.deepcopy(template))
        overrides[record_name] = {
            "target_organism": copy.deepcopy(model.get("target_organism")),
            "hosts": copy.deepcopy(model.get("hosts") or []),
            "related_species": copy.deepcopy(
                model.get("related_species") or []
            ),
            "background_organisms": copy.deepcopy(
                model.get("background_organisms") or []
            ),
            "design_parameters": copy.deepcopy(
                model.get("design_parameters") or {}
            ),
        }
    return {
        "task_name": task_name,
        "sequence_mode": "independent_targets",
        "template_records": records,
        "batch_defaults": {},
        "target_overrides": overrides,
    }


def rebind_preflight_target_ids(
    preflight: dict[str, Any],
    source_targets: list[dict[str, Any]],
) -> dict[str, Any]:
    """Keep logical target IDs stable across a task version."""
    rebound = copy.deepcopy(preflight)
    fresh_targets = rebound.get("targets") or []
    if len(fresh_targets) != len(source_targets):
        raise InputValidationError(
            "M7 预检靶标数与源批次不一致，不能安全复用",
            details={
                "source_target_total": len(source_targets),
                "preflight_target_total": len(fresh_targets),
            },
        )
    id_map = {
        str(fresh["target_id"]): str(source["target_id"])
        for fresh, source in zip(fresh_targets, source_targets)
    }

    def rebind_identifier(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        for old, new in id_map.items():
            if value == old:
                return new
            if value.startswith(f"{old}:"):
                return f"{new}{value[len(old):]}"
        return value

    def rebind_target(target: dict[str, Any]) -> None:
        old = str(target.get("target_id"))
        target["target_id"] = id_map[old]
        template = target.get("template") or {}
        template["template_id"] = rebind_identifier(
            template.get("template_id")
        )
        for collection in (
            "target_object",
            "host_objects",
            "related_objects",
            "background_objects",
        ):
            items = (
                [target[collection]]
                if isinstance(target.get(collection), dict)
                else target.get(collection) or []
            )
            for item in items:
                if isinstance(item, dict):
                    item["object_id"] = rebind_identifier(item.get("object_id"))
        for issue in target.get("issues") or []:
            if isinstance(issue, dict):
                issue["target_id"] = rebind_identifier(issue.get("target_id"))

    for target in fresh_targets:
        rebind_target(target)
    for issue in rebound.get("issues") or []:
        if isinstance(issue, dict):
            issue["target_id"] = rebind_identifier(issue.get("target_id"))
    manifest = rebound.get("target_manifest")
    if isinstance(manifest, dict):
        manifest["targets"] = fresh_targets
        manifest["ready_target_ids"] = [
            rebind_identifier(value)
            for value in manifest.get("ready_target_ids") or []
        ]
        manifest["problem_target_ids"] = [
            rebind_identifier(value)
            for value in manifest.get("problem_target_ids") or []
        ]
    rebound["input_fingerprint"] = stable_sha256(
        {
            "input": rebound.get("normalised_input") or {},
            "targets": fresh_targets,
        }
    )
    return rebound
