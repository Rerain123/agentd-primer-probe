"""Read-only provenance for the existing tools bound to WF-05.

This module contains metadata only.  It does not implement a tool, algorithm,
fallback, or workflow step.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any


_EXPERT_CONTRACT = {
    "source": "AgentD_\u5f15\u7269\u63a2\u9488\u667a\u80fd\u4f53_\u5de5\u5177+\u5de5\u4f5c\u6d41/AgentD_\u5f15\u7269\u63a2\u9488\u667a\u80fd\u4f53_\u89c4\u5219\u66f4\u65b0_\u7b80\u5e76_\u7248.md",
    "sha256": "65eaa4af1bc95a93ac13dba1e963b7491613e97ecee885c6b1247549e67a081c",
    "mode": "READ_ONLY",
}

_TOOLS = {
    "C0": {
        "implementation": "database.DatabaseCatalog",
        "binding": "EXACT_EXISTING_TOOL_SNAPSHOT",
        "source": "src/agentd/database.py",
        "snapshot": "agentd/database.py",
        "sha256": "31e806460a1e1620cff00fca309bcf6a23a20ef73068c7441e720aa94fbe5d8e",
        "modified": False,
    },
    "C5": {
        "implementation": "alignment.MafftAdapter",
        "binding": "EXACT_EXISTING_TOOL_SNAPSHOT",
        "source": "src/agentd/alignment.py",
        "snapshot": "agentd/alignment.py",
        "sha256": "a983bc4676ebf3311ec0b47195ced1e3d41f2606c0d564b8716ad3ed51c7656b",
        "modified": False,
    },
    "C7": {
        "implementation": "schema.assemble",
        "binding": "EXACT_EXISTING_TOOL_SNAPSHOT",
        "source": "src/agentd/schema.py",
        "snapshot": "agentd/schema.py",
        "sha256": "4d1a31a978e809ffdca2dd7b31de087328dd5ed4b06aee043ef462b3ab5a9c44",
        "modified": False,
    },
    "P1": {
        "implementation": "primer3_tool.Primer3Adapter",
        "binding": "EXACT_EXISTING_TOOL_SNAPSHOT",
        "source": "src/agentd/primer3_tool.py",
        "snapshot": "agentd/primer3_tool.py",
        "sha256": "33a4ffa62c9e793ba967b7a75306e275efdc02719ac48d86ed4c8b5ba221067c",
        "modified": False,
        "expert_tool_document_sha256": "85c3d4bc76c72a9a13fe363049df671f3a590a4c76f8090926966f8640b32ed4",
    },
    "C21": {
        "implementation": None,
        "binding": "NOT_AVAILABLE_NOT_EVALUATED",
        "source": None,
        "snapshot": None,
        "sha256": None,
        "modified": False,
        "required": False,
    },
}


def tool_binding_provenance() -> dict[str, Any]:
    """Return an isolated copy of the read-only binding declaration."""

    return {
        "expert_contract": copy.deepcopy(_EXPERT_CONTRACT),
        "tools": copy.deepcopy(_TOOLS),
    }


def verify_existing_tool_snapshots() -> None:
    """Fail closed when a bound existing-tool snapshot has been changed."""

    package_root = Path(__file__).resolve().parent
    failures: list[dict[str, str]] = []
    for tool_id, binding in _TOOLS.items():
        if binding.get("binding") != "EXACT_EXISTING_TOOL_SNAPSHOT":
            continue
        relative_path = str(binding["snapshot"])
        if relative_path.startswith("agentd/"):
            relative_path = relative_path[len("agentd/") :]
        path = package_root / relative_path
        actual = (
            hashlib.sha256(path.read_bytes()).hexdigest()
            if path.is_file()
            else "MISSING"
        )
        expected = str(binding["sha256"])
        if actual != expected:
            failures.append(
                {
                    "tool": tool_id,
                    "path": relative_path,
                    "expected_sha256": expected,
                    "actual_sha256": actual,
                }
            )
    if failures:
        raise RuntimeError(f"WF-05 existing-tool snapshot integrity failure: {failures}")
