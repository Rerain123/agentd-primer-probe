from __future__ import annotations

import re
import unittest
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SOURCE_MATERIALS = DOCS / "source-materials"
LOCAL_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
def current_markdown_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "AGENTS.md"]
    for path in DOCS.rglob("*.md"):
        if SOURCE_MATERIALS / "legacy-agentd-design" in path.parents:
            continue
        files.append(path)
    files.append(ROOT / "deploy" / "docker" / "README.md")
    return sorted(set(files))


def local_link_target(raw_target: str) -> str | None:
    target = raw_target.strip()
    if target.startswith("<") and ">" in target:
        target = target[1 : target.index(">")]
    else:
        target = re.sub(r'\s+["\'].*$', "", target)
    if target.startswith(("#", "http://", "https://", "mailto:", "data:")):
        return None
    target = unquote(target.split("#", 1)[0].split("?", 1)[0])
    return target or None


class RepositoryLayoutTests(unittest.TestCase):
    def test_agents_is_a_small_router(self) -> None:
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        line_count = len(agents.splitlines())
        self.assertGreaterEqual(line_count, 70)
        self.assertLessEqual(line_count, 120)
        for required_route in (
            "docs/architecture/REPOSITORY_LAYOUT.md",
            "docs/workflows/README.md",
            "docs/operations/README.md",
            "docs/source-materials/README.md",
        ):
            self.assertIn(required_route, agents)

    def test_product_readme_stays_product_facing(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("## 产品能力", readme)
        self.assertIn("## 输入与交付", readme)
        self.assertNotIn("## HTTP API", readme)
        self.assertNotIn("POST /v1/", readme)

    def test_wf01_route_discloses_both_primer3_only_paths(self) -> None:
        guide = (
            DOCS / "workflows" / "wf01" / "WF01_CURRENT_IMPLEMENTATION.md"
        ).read_text(encoding="utf-8")
        for required_marker in (
            'R -->|"只使用 Primer3"|',
            "POST /v1/tools/primer3/design",
            "P1_Primer3_DESIGN",
            'M4A -.->|"DESIGN_ONLY（历史/底层兼容批次）"|',
        ):
            self.assertIn(required_marker, guide)

    def test_docs_are_classified(self) -> None:
        top_level_markdown = sorted(path.name for path in DOCS.glob("*.md"))
        self.assertEqual(top_level_markdown, ["README.md"])
        for category in (
            "architecture",
            "workflows",
            "operations",
            "reference",
            "source-materials",
            "history",
        ):
            self.assertTrue((DOCS / category / "README.md").is_file(), category)

    def test_required_current_entries_exist(self) -> None:
        required = (
            "architecture/PROJECT_ENGINEERING_GUIDE.md",
            "architecture/REPOSITORY_LAYOUT.md",
            "architecture/WORKFLOW_ISOLATION_ARCHITECTURE.md",
            "workflows/wf01/WF01_CURRENT_IMPLEMENTATION.md",
            "workflows/wf02/WF02_V0.1_IMPLEMENTATION_PLAN.md",
            "workflows/wf03/WF03_CURRENT_IMPLEMENTATION.md",
            "workflows/wf04/README.md",
            "workflows/wf05/WF05_CURRENT_IMPLEMENTATION.md",
            "operations/MCP_SERVER.md",
            "operations/SERVER_INSTALL.md",
            "history/CHANGELOG.md",
        )
        for relative_path in required:
            self.assertTrue((DOCS / relative_path).is_file(), relative_path)

    def test_current_local_markdown_links_resolve(self) -> None:
        failures: list[str] = []
        for markdown_file in current_markdown_files():
            text = markdown_file.read_text(encoding="utf-8")
            for match in LOCAL_LINK.finditer(text):
                target = local_link_target(match.group(1))
                if target is None:
                    continue
                destination = (markdown_file.parent / target).resolve()
                if not destination.exists():
                    source = markdown_file.relative_to(ROOT).as_posix()
                    failures.append(f"{source} -> {target}")
        self.assertEqual(failures, [], "Broken local links:\n" + "\n".join(failures))

    def test_expert_materials_are_excluded_from_github(self) -> None:
        self.assertEqual(
            sorted(path.relative_to(SOURCE_MATERIALS).as_posix()
                   for path in SOURCE_MATERIALS.rglob("*") if path.is_file()),
            ["README.md"],
        )
        self.assertFalse(
            (DOCS / "history" / "WF01_WF02_EXPERT_REVIEW_20261013.md").exists()
        )


if __name__ == "__main__":
    unittest.main()
