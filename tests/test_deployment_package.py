from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "deploy" / "scripts" / "validate_catalog.py"
SKILLS_ROOT = ROOT / "skills"
EXPECTED_SKILLS = {
    "agentd-p1-primer3": "P1",
    "agentd-p2-specificity": "P2",
    "agentd-p3-mgb": "P3",
    "agentd-p4-multiplex": "P4",
    "agentd-p5-internal-control": "P5",
    "agentd-p6-viennarna": "P6",
}
WORKFLOW_SKILLS = {
    "oligosmith-wf01",
    "oligosmith-wf02",
    "oligosmith-wf03",
    "oligosmith-wf04",
    "oligosmith-wf05",
}


class DeploymentPackageTests(unittest.TestCase):
    def test_p1_to_p6_skill_packages_are_complete(self) -> None:
        actual_skills = {path.name for path in SKILLS_ROOT.iterdir() if path.is_dir()}
        self.assertEqual(actual_skills, set(EXPECTED_SKILLS) | WORKFLOW_SKILLS)

        for skill_name, layer in EXPECTED_SKILLS.items():
            skill_dir = SKILLS_ROOT / skill_name
            skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            contract_text = (skill_dir / "references" / "contract.md").read_text(encoding="utf-8")
            ui = yaml.safe_load((skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8"))

            self.assertTrue(skill_text.startswith(f"---\nname: {skill_name}\n"))
            self.assertIn(layer, skill_text)
            self.assertNotIn("TODO", skill_text)
            self.assertGreater(len(contract_text.strip()), 200)
            self.assertGreater(
                sum("\u4e00" <= char <= "\u9fff" for char in skill_text),
                100,
                f"{skill_name}/SKILL.md 应以中文说明为主",
            )
            self.assertGreater(
                sum("\u4e00" <= char <= "\u9fff" for char in contract_text),
                40,
                f"{skill_name}/references/contract.md 应以中文说明为主",
            )
            self.assertEqual(ui["interface"]["display_name"].split()[2], layer)
            self.assertIn(f"${skill_name}", ui["interface"]["default_prompt"])
            for field in ("display_name", "short_description", "default_prompt"):
                self.assertRegex(
                    ui["interface"][field],
                    r"[\u4e00-\u9fff]",
                    f"{skill_name} 的 {field} 应包含中文",
                )
            self.assertGreaterEqual(len(ui["interface"]["short_description"]), 25)
            self.assertLessEqual(len(ui["interface"]["short_description"]), 64)

    def test_release_manifest_matches_package_version(self) -> None:
        release = json.loads((ROOT / "deploy" / "release-manifest.json").read_text(encoding="utf-8"))
        package_version = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]["version"]
        self.assertEqual(package_version, "1.2.13")
        self.assertEqual(release["version"], package_version)
        self.assertEqual(release["schema_version"], "3.0.0-phase3")
        self.assertEqual(release["mcp_endpoint"], "/mcp")
        self.assertIn("exact primer_design_output data/type", release["himat_terminal_result"])
        self.assertEqual(
            release["mcp_scoped_endpoints"],
            {
                "wf01": "/mcp/wf01/",
                "wf02": "/mcp/wf02/",
                "wf03": "/mcp/wf03/",
                "wf04": "/mcp/wf04/",
                "wf05": "/mcp/wf05/",
            },
        )
        self.assertEqual(
            set(release["workflow_skills"]),
            WORKFLOW_SKILLS,
        )
        wf05_tool_policy = release["wf05_tool_policy"]
        self.assertEqual(wf05_tool_policy["mode"], "existing-tools-only")
        self.assertEqual(
            set(wf05_tool_policy["exact_snapshot_sha256"]),
            {"C0", "C5", "C7", "P1"},
        )
        self.assertEqual(wf05_tool_policy["not_evaluated"], ["C21"])
        self.assertIn("引物与探针设计", release["description"])
        self.assertIn("WF-02 attachment cards use configured RustFS public URLs", release["authentication"])
        package_init = (ROOT / "src" / "agentd" / "__init__.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(f'__version__ = "{package_version}"', package_init)

        package_script = (ROOT / "deploy" / "build-package.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn('$optionalSourceRoots = @("examples")', package_script)
        self.assertIn('"AGENTS.md"', package_script)
        self.assertIn('"src\\agentd_workflow_snapshots"', package_script)
        self.assertIn(
            'throw "Required package directory is missing: $sourceRoot"',
            package_script,
        )

        mcp_docs = (ROOT / "docs" / "operations" / "MCP_SERVER.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            '"description": "引物与探针设计、验证、优化和报告生成"',
            mcp_docs,
        )
        self.assertNotIn(
            "qPCR primer and probe design, validation, optimization, and reporting",
            mcp_docs,
        )

        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn(
            'org.opencontainers.image.description="OligoSmith 可流式传输 HTTP MCP 服务"',
            dockerfile,
        )

        env_example = (ROOT / "deploy" / ".env.example").read_text(
            encoding="utf-8"
        )
        compose = (ROOT / "deploy" / "docker-compose.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(f"AGENTD_IMAGE_TAG={package_version}", env_example)
        self.assertIn(f"AGENTD_IMAGE_TAG:-{package_version}", compose)
        self.assertIn("MCP_MAX_TOOL_RESULT_BYTES=65536", env_example)
        self.assertIn("MCP_MAX_TOOL_RESULT_BYTES:-65536", compose)
        self.assertNotIn("MCP_HIMAT_DIRECT_RESULT_COMPAT", env_example)
        self.assertNotIn("MCP_HIMAT_DIRECT_RESULT_COMPAT", compose)
        self.assertIn("MCP_WF02_SYNC_WAIT_SECONDS=235", env_example)
        self.assertIn("MCP_WF02_SYNC_WAIT_SECONDS:-235", compose)
        self.assertIn("WF02_BACKGROUND_WORKERS=1", env_example)
        self.assertIn("WF02_BACKGROUND_WORKERS:-1", compose)
        self.assertNotIn("MCP_WF02_MAX_INTERNAL_UNITS", env_example)
        self.assertNotIn("MCP_WF02_MAX_EXECUTION_SECONDS", env_example)
        self.assertNotIn("MCP_DESIGN_INPUT_DEDUP_SECONDS", env_example)
        self.assertNotIn("MCP_DESIGN_INPUT_DEDUP_SECONDS", compose)
        self.assertIn(
            "BLAST_SEARCH_SERVICE_URL=http://host.docker.internal:8090",
            env_example,
        )
        self.assertIn("BLAST_SEARCH_SERVICE_URL:-", compose)
        self.assertIn("host.docker.internal:host-gateway", compose)
        self.assertIn("RUSTFS_ENDPOINT_URL=https://rustfs.ne.scqcyz.cn", env_example)
        self.assertIn("RUSTFS_BUCKET=wens", env_example)
        self.assertIn(
            "RUSTFS_PUBLIC_BASE_URL=https://rustfs.ne.scqcyz.cn",
            env_example,
        )
        self.assertIn("RUSTFS_PUBLIC_BASE_URL:-https://rustfs.ne.scqcyz.cn", compose)
        self.assertNotIn("RUSTFS_FILE_URL_PREFIX", compose)
        self.assertIn("NCBI_ENTREZ_EMAIL=", env_example)
        self.assertIn("NCBI_ENTREZ_EMAIL:-", compose)

        catalog = json.loads(
            (ROOT / "deploy" / "server-config" / "database-catalog.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(
            any(key.startswith("internal:") for key in catalog["resources"]),
            "P5默认管家基因由Entrez获取，部署目录不应强制声明独立ACTB资源",
        )

        verify = (ROOT / "deploy" / "scripts" / "verify.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("names != required", verify)
        self.assertNotIn('"get_wf01_scientific_artifacts"', verify)
        self.assertNotIn('"prepare_wf01_design_input"', verify)
        self.assertIn('"submit_wf01_interaction_response"', verify)
        self.assertIn('"run_wf01_until_blocked"', verify)
        self.assertIn('"get_wf01_candidate"', verify)
        self.assertNotIn('"get_agent_audit_log"', verify)
        self.assertIn('"download_himat_file"', verify)
        self.assertIn('"get_wf01_export"', verify)
        self.assertIn('"create_wf02_batch"', verify)
        self.assertIn('"run_wf02_until_blocked"', verify)
        self.assertIn('"create_wf02_version"', verify)
        self.assertIn('"get_wf02_report"', verify)
        self.assertIn('"get_wf02_panel"', verify)
        self.assertIn('"get_wf02_export"', verify)
        self.assertIn('"run_wf03_internal_control"', verify)
        self.assertIn('"retry_wf03_internal_control"', verify)
        self.assertIn('"get_wf03_report"', verify)
        self.assertIn("WF-03 tool routing contract is stale", verify)
        self.assertIn("WF-03 tool schema is stale", verify)
        self.assertIn("WF-03 download routing contract is stale", verify)
        self.assertIn("禁止在调用前分析", verify)
        self.assertIn('"run_wf04_validation"', verify)
        self.assertIn('"retry_wf04_validation"', verify)
        self.assertIn('"get_wf04_report"', verify)
        self.assertIn("WF-04 tool routing contract is stale", verify)
        self.assertIn("WF-04 tool schema is stale", verify)
        self.assertIn("WF-04 download routing contract is stale", verify)
        self.assertIn('"run_wf05_variation"', verify)
        self.assertIn('"retry_wf05_variation"', verify)
        self.assertIn('"get_wf05_report"', verify)
        self.assertIn("WF-05 tool routing contract is stale", verify)
        self.assertIn("WF-05 tool schema is stale", verify)
        self.assertIn("WF-05 download routing contract is stale", verify)
        self.assertIn("顶层input_artifact_id原样传入", verify)
        self.assertIn("workflow-scoped MCP endpoints reject missing JWT", verify)
        self.assertIn("scoped_required", verify)
        self.assertIn("unexpected scoped MCP tool list", verify)
        self.assertIn("scoped MCP instructions are too long", verify)
        self.assertIn("scoped MCP repeats terminal schema description", verify)
        self.assertIn("verify_chinese_metadata", verify)
        self.assertIn("MCP schema contains generated display title", verify)
        self.assertIn("MCP parameter description is not Chinese", verify)
        self.assertIn("WF-02 report downloads reject missing JWT", verify)
        self.assertNotIn('"design_primers_with_primer3"', verify)
        self.assertNotIn('"run_wf01_stage",', verify)

        installer = (ROOT / "deploy" / "scripts" / "install.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("agentd_read_env AGENTD_REQUIRE_DATABASES true", installer)
        self.assertIn("允许缺少数据库启动", installer)
        self.assertIn('bash "${SCRIPT_DIR}/preflight.sh"', installer)
        self.assertIn('bash "${SCRIPT_DIR}/verify.sh"', installer)

    def test_remote_blast_can_satisfy_required_cross_background(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-deploy-remote-blast-") as temp_dir:
            root = Path(temp_dir)
            deploy = root / "deploy"
            deploy.mkdir(parents=True)
            env_file = deploy / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "AGENTD_BLASTDB_DIR=./blast",
                        "AGENTD_SEQUENCE_DATA_DIR=./sequences",
                        "BLAST_SEARCH_SERVICE_URL=http://host.docker.internal:8090",
                    ]
                ),
                encoding="utf-8",
            )
            catalog = deploy / "catalog.json"
            catalog.write_text(
                json.dumps(
                    {
                        "resources": {
                            "cross:default:blast": {
                                "kind": "blast_database",
                                "path": "/data/blastdb/cross/current/db",
                                "version": "SET_ON_SERVER",
                                "required": True,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(VALIDATOR),
                    "--env-file",
                    str(env_file),
                    "--catalog",
                    str(catalog),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("远程 NCBI BLAST MCP", completed.stdout)

    def test_server_catalog_validator_accepts_complete_required_resources(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-deploy-validator-") as temp_dir:
            root = Path(temp_dir)
            deploy = root / "deploy"
            blast_prefix = deploy / "data" / "blast" / "host" / "db"
            blast_prefix.parent.mkdir(parents=True)
            for suffix in (".nhr", ".nin", ".nsq"):
                Path(f"{blast_prefix}{suffix}").write_bytes(b"index")
            env_file = deploy / ".env"
            env_file.write_text(
                "AGENTD_BLASTDB_DIR=./data/blast\nAGENTD_SEQUENCE_DATA_DIR=./data/sequences\n",
                encoding="utf-8",
            )
            catalog = deploy / "catalog.json"
            catalog.write_text(
                json.dumps(
                    {
                        "resources": {
                            "host:pig:blast": {
                                "kind": "blast_database",
                                "path": "/data/blastdb/host/db",
                                "version": "fixture-1",
                                "required": True,
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [sys.executable, str(VALIDATOR), "--env-file", str(env_file), "--catalog", str(catalog)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("目录检查通过", completed.stdout)

    def test_catalog_validator_can_select_new_optional_resources(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-deploy-selected-resource-") as temp_dir:
            root = Path(temp_dir)
            deploy = root / "deploy"
            blast_prefix = deploy / "blast" / "target" / "asfv" / "db"
            blast_prefix.parent.mkdir(parents=True)
            for suffix in (".nhr", ".nin", ".nsq"):
                Path(f"{blast_prefix}{suffix}").write_bytes(b"index")
            sequence_file = blast_prefix.parent / "source.fasta"
            sequence_file.write_text(">NC_001659.2\nACGTACGT\n", encoding="utf-8")

            env_file = deploy / ".env"
            env_file.write_text(
                "AGENTD_BLASTDB_DIR=./blast\nAGENTD_SEQUENCE_DATA_DIR=./sequences\n",
                encoding="utf-8",
            )
            catalog = deploy / "catalog.json"
            catalog.write_text(
                json.dumps(
                    {
                        "resources": {
                            "host:cattle:blast": {
                                "kind": "blast_database",
                                "path": "/data/blastdb/host/cattle/current/db",
                                "version": "SET_ON_SERVER",
                                "required": False,
                            },
                            "target:ASFV:blast": {
                                "kind": "blast_database",
                                "path": "/data/blastdb/target/asfv/db",
                                "version": "NC_001659.2",
                                "required": False,
                            },
                            "target:ASFV:sequences": {
                                "kind": "fasta",
                                "path": "/data/blastdb/target/asfv/source.fasta",
                                "version": "NC_001659.2",
                                "required": False,
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(VALIDATOR),
                    "--env-file",
                    str(env_file),
                    "--catalog",
                    str(catalog),
                    "--resource-key",
                    "target:ASFV:blast",
                    "--resource-key",
                    "target:ASFV:sequences",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("target:ASFV:blast", completed.stdout)
        self.assertIn("target:ASFV:sequences", completed.stdout)
        self.assertNotIn("host:cattle:blast", completed.stderr)


if __name__ == "__main__":
    unittest.main()
