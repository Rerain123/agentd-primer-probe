from __future__ import annotations

import ast
import hashlib
import importlib
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


SNAPSHOTS = (
    (
        "wf02_wf01_v1",
        "1.1.10",
        "66a000c4857ce598962011708c49eba392bcf052cb92b22b51f8ea5a6d4b0342",
    ),
    (
        "wf03_v1",
        "1.1.13",
        "19df30aa9fbbc73ea9511a2ca3c50ecd90b2e4148c6368256292d2484277b05c",
    ),
    (
        "wf04_v1",
        "1.1.13",
        "19df30aa9fbbc73ea9511a2ca3c50ecd90b2e4148c6368256292d2484277b05c",
    ),
)

WF05_SNAPSHOT_FILES = {
    "__init__.py",
    "agentd/__init__.py",
    "agentd/alignment.py",
    "agentd/config.py",
    "agentd/database.py",
    "agentd/errors.py",
    "agentd/models.py",
    "agentd/object_storage.py",
    "agentd/primer3_tool.py",
    "agentd/rules.py",
    "agentd/schema.py",
    "agentd/sequence.py",
    "agentd/wf05_identifiers.py",
    "agentd/wf05_report_html.py",
    "agentd/wf05_reports.py",
    "agentd/wf05_ui_messages.py",
    "agentd/wf05_tool_provenance.py",
    "agentd/workflow_delivery.py",
    "agentd/workflows/__init__.py",
    "agentd/workflows/wf05_v01.py",
}
WF05_SNAPSHOT_SHA256 = (
    "ae0bdb541ff33730aec4a8b0e4d4a74372f296a32171090c2d76f39c031fb759"
)


def _source_files(release: str) -> tuple[Path, dict[str, bytes]]:
    archive = ROOT / "dist" / f"agentd-primer-probe-server-{release}.zip"
    prefix = f"agentd-primer-probe-server-{release}/src/agentd/"
    with zipfile.ZipFile(archive) as package:
        files = {
            name[len(prefix) :]: package.read(name)
            for name in package.namelist()
            if name.startswith(prefix) and not name.endswith("/")
        }
    return archive, files


def test_snapshot_archives_and_python_files_are_immutable() -> None:
    for package_name, release, expected_archive_hash in SNAPSHOTS:
        archive, source_files = _source_files(release)
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == expected_archive_hash
        metadata = importlib.import_module(
            f"agentd_workflow_snapshots.{package_name}"
        )
        hotfix_files = getattr(metadata, "HOTFIX_FILES_SHA256", {})
        development_files = getattr(metadata, "DEVELOPMENT_FILES_SHA256", {})
        assert not (set(hotfix_files) & set(development_files))
        declared_differences = {**hotfix_files, **development_files}

        package_root = (
            ROOT
            / "src"
            / "agentd_workflow_snapshots"
            / package_name
            / "agentd"
        )
        local_files = {
            path.relative_to(package_root).as_posix(): path
            for path in package_root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        }
        assert set(local_files) == set(source_files)
        for relative_path, source_content in source_files.items():
            if relative_path == "__init__.py":
                # The sole bootstrap patch prevents importing the copied
                # application service as a side effect of loading a runtime.
                bootstrap = local_files[relative_path].read_text(encoding="utf-8")
                assert "from .service import" not in bootstrap
                assert f'__version__ = "{release}"' in bootstrap
                continue
            if relative_path in declared_differences:
                assert package_name == "wf04_v1"
                assert (
                    hashlib.sha256(local_files[relative_path].read_bytes()).hexdigest()
                    == declared_differences[relative_path]
                )
                assert local_files[relative_path].read_bytes() != source_content
                continue
            assert local_files[relative_path].read_bytes() == source_content


def test_snapshot_metadata_matches_source_archives() -> None:
    for package_name, release, expected_archive_hash in SNAPSHOTS:
        metadata = importlib.import_module(
            f"agentd_workflow_snapshots.{package_name}"
        )
        assert metadata.IMMUTABLE is True
        assert metadata.SOURCE_RELEASE == release
        assert metadata.SOURCE_ARCHIVE_SHA256 == expected_archive_hash
        assert metadata.BOOTSTRAP_PATCH == "REMOVE_EAGER_SERVICE_IMPORT"


def test_wf04_hotfix_is_bounded_to_registered_runtime_files() -> None:
    metadata = importlib.import_module("agentd_workflow_snapshots.wf04_v1")

    assert metadata.HOTFIX_REVISION == "wf04-dg-unit-r09-report-2026-08-28"
    assert set(metadata.HOTFIX_FILES_SHA256) == {
        "primer3_tool.py",
        "workflow_report_html.py",
    }


def test_wf04_direct_development_edit_is_bounded_to_its_workflow() -> None:
    metadata = importlib.import_module("agentd_workflow_snapshots.wf04_v1")

    assert metadata.DEVELOPMENT_REVISION == "wf04-step-order-2026-08-28"
    assert set(metadata.DEVELOPMENT_FILES_SHA256) == {
        "workflows/wf04_v01.py",
    }
    runtime_workflow = (
        ROOT
        / "src"
        / "agentd_workflow_snapshots"
        / "wf04_v1"
        / "agentd"
        / "workflows"
        / "wf04_v01.py"
    )
    development_workflow = (
        ROOT / "src" / "agentd" / "workflows" / "wf04_v01.py"
    )
    assert runtime_workflow.read_bytes() == development_workflow.read_bytes()
    assert (
        hashlib.sha256(runtime_workflow.read_bytes()).hexdigest()
        == metadata.DEVELOPMENT_FILES_SHA256["workflows/wf04_v01.py"]
    )


def test_wf05_snapshot_file_set_and_content_are_immutable() -> None:
    package_root = ROOT / "src" / "agentd_workflow_snapshots" / "wf05_v1"
    files = sorted(
        package_root.rglob("*.py"),
        key=lambda path: path.relative_to(package_root).as_posix(),
    )
    assert {
        path.relative_to(package_root).as_posix() for path in files
    } == WF05_SNAPSHOT_FILES

    digest = hashlib.sha256()
    for path in files:
        relative_path = path.relative_to(package_root).as_posix()
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    assert digest.hexdigest() == WF05_SNAPSHOT_SHA256

    metadata = importlib.import_module("agentd_workflow_snapshots.wf05_v1")
    assert metadata.IMMUTABLE is True
    assert metadata.SNAPSHOT_VERSION == "wf05-runtime-snapshot-v1"
    assert metadata.SOURCE_BASE == "agentd-primer-probe-2026-08-26"


def test_snapshot_code_has_no_absolute_import_back_to_mutable_package() -> None:
    snapshot_root = ROOT / "src" / "agentd_workflow_snapshots"
    violations: list[str] = []
    for path in snapshot_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level == 0 and str(node.module or "").startswith("agentd"):
                    violations.append(f"{path}:{node.lineno}")
            elif isinstance(node, ast.Import):
                if any(alias.name.startswith("agentd") for alias in node.names):
                    violations.append(f"{path}:{node.lineno}")
    assert violations == []


def test_wf05_uses_exact_existing_tool_snapshots_without_custom_tools() -> None:
    from agentd_workflow_snapshots.wf05_v1.agentd.wf05_tool_provenance import (
        tool_binding_provenance,
        verify_existing_tool_snapshots,
    )

    source_root = ROOT / "src" / "agentd"
    snapshot_root = (
        ROOT / "src" / "agentd_workflow_snapshots" / "wf05_v1" / "agentd"
    )
    tool_files = {
        "C0": "database.py",
        "C5": "alignment.py",
        "C7": "schema.py",
        "P1": "primer3_tool.py",
    }
    provenance = tool_binding_provenance()
    assert provenance["expert_contract"]["mode"] == "READ_ONLY"
    for tool_id, file_name in tool_files.items():
        source = source_root / file_name
        snapshot = snapshot_root / file_name
        assert snapshot.read_bytes() == source.read_bytes()
        digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        binding = provenance["tools"][tool_id]
        assert binding["binding"] == "EXACT_EXISTING_TOOL_SNAPSHOT"
        assert binding["modified"] is False
        assert binding["sha256"] == digest

    c21 = provenance["tools"]["C21"]
    assert c21["binding"] == "NOT_AVAILABLE_NOT_EVALUATED"
    assert c21["implementation"] is None
    assert c21["required"] is False
    verify_existing_tool_snapshots()

    workflow_path = snapshot_root / "workflows" / "wf05_v01.py"
    workflow_tree = ast.parse(
        workflow_path.read_text(encoding="utf-8"),
        filename=str(workflow_path),
    )
    forbidden_classes = [
        node.name
        for node in ast.walk(workflow_tree)
        if isinstance(node, ast.ClassDef)
        and node.name != "WF05VariationToleranceV01"
        and (node.name.endswith("Adapter") or node.name.endswith("Tool"))
    ]
    assert forbidden_classes == []
    assert not (snapshot_root / "wf05_alignment.py").exists()
    assert not (snapshot_root / "wf05_catalog.py").exists()
