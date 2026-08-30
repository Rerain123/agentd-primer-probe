from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills"
SCOPES = ("wf01", "wf02", "wf03", "wf04", "wf05")


def _frontmatter(text: str) -> dict:
    assert text.startswith("---\n")
    _, raw, _ = text.split("---", 2)
    return yaml.safe_load(raw)


def test_workflow_skills_are_discriminating_and_bound_to_scoped_mcp():
    descriptions: set[str] = set()
    for scope in SCOPES:
        skill_name = f"oligosmith-{scope}"
        skill_dir = SKILL_ROOT / skill_name
        skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        metadata = _frontmatter(skill_text)
        config = yaml.safe_load(
            (skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8")
        )

        assert metadata["name"] == skill_name
        assert scope.upper().replace("WF", "WF-") in metadata["description"]
        assert metadata["description"] not in descriptions
        descriptions.add(metadata["description"])
        assert len(skill_text) < 2200
        assert "不调用其他工作流的工具" in skill_text
        assert "终态卡片已由工具直接交付" in skill_text
        assert "不得生成助手文本" in skill_text
        assert "content[0].text" not in skill_text
        assert "不得再次调用工具" in skill_text

        interface = config["interface"]
        assert interface["default_prompt"].startswith(f"使用 ${skill_name}")
        assert 25 <= len(interface["short_description"]) <= 64
        assert config["policy"]["allow_implicit_invocation"] is True

        dependencies = config["dependencies"]["tools"]
        assert len(dependencies) == 1
        dependency = dependencies[0]
        assert dependency == {
            "type": "mcp",
            "value": skill_name,
            "description": (
                f"OligoSmith {scope.upper().replace('WF', 'WF-')} scoped MCP server"
            ),
            "transport": "streamable_http",
            "url": f"https://oligosmith.ne.scqcyz.cn/mcp/{scope}/",
        }
