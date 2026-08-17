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


class DeploymentPackageTests(unittest.TestCase):
    def test_p1_to_p6_skill_packages_are_complete(self) -> None:
        actual_skills = {path.name for path in SKILLS_ROOT.iterdir() if path.is_dir()}
        self.assertEqual(actual_skills, set(EXPECTED_SKILLS))

        for skill_name, layer in EXPECTED_SKILLS.items():
            skill_dir = SKILLS_ROOT / skill_name
            skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            contract_text = (skill_dir / "references" / "contract.md").read_text(encoding="utf-8")
            ui = yaml.safe_load((skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8"))

            self.assertTrue(skill_text.startswith(f"---\nname: {skill_name}\n"))
            self.assertIn(layer, skill_text)
            self.assertNotIn("TODO", skill_text)
            self.assertGreater(len(contract_text.strip()), 200)
            self.assertEqual(ui["interface"]["display_name"].split()[2], layer)
            self.assertIn(f"${skill_name}", ui["interface"]["default_prompt"])

    def test_release_manifest_matches_package_version(self) -> None:
        release = json.loads((ROOT / "deploy" / "release-manifest.json").read_text(encoding="utf-8"))
        package_version = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]["version"]
        self.assertEqual(package_version, "1.0.3")
        self.assertEqual(release["version"], package_version)
        self.assertEqual(release["schema_version"], "3.0.0-phase3")
        self.assertIn("引物与探针设计", release["description"])
        package_init = (ROOT / "src" / "agentd" / "__init__.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(f'__version__ = "{package_version}"', package_init)

        mcp_docs = (ROOT / "docs" / "MCP_SERVER.md").read_text(
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

        dockerfile = (ROOT / "deploy" / "docker" / "Dockerfile").read_text(
            encoding="utf-8"
        )
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
        self.assertIn("MCP_DESIGN_INPUT_DEDUP_SECONDS=10", env_example)
        self.assertIn("MCP_DESIGN_INPUT_DEDUP_SECONDS:-10", compose)
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

        verify = (ROOT / "deploy" / "scripts" / "verify.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("names != required", verify)
        self.assertNotIn('"get_wf01_scientific_artifacts"', verify)
        self.assertIn('"prepare_wf01_design_input"', verify)
        self.assertIn('"submit_wf01_interaction_response"', verify)
        self.assertIn('"run_wf01_until_blocked"', verify)
        self.assertIn('"get_wf01_candidate"', verify)
        self.assertNotIn('"get_agent_audit_log"', verify)
        self.assertIn('"download_himat_file"', verify)
        self.assertIn('"get_wf01_export"', verify)
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
            sequence_file = deploy / "data" / "sequences" / "internal" / "ACTB.fasta"
            blast_prefix.parent.mkdir(parents=True)
            sequence_file.parent.mkdir(parents=True)
            for suffix in (".nhr", ".nin", ".nsq"):
                Path(f"{blast_prefix}{suffix}").write_bytes(b"index")
            sequence_file.write_text(
                "".join(f">strain_{index}\n{'ATGC' * 60}\n" for index in range(1, 6)),
                encoding="utf-8",
            )
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
                            "internal:pig:ACTB:sequences": {
                                "kind": "fasta",
                                "path": "/data/sequences/internal/ACTB.fasta",
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


if __name__ == "__main__":
    unittest.main()
