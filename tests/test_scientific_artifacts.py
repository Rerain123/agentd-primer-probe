from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.service import AgentDService


def valid_payload() -> dict:
    return {
        "task_name": "scientific-artifact-fixture",
        "fasta_text": ">target_001\n" + "ACGT" * 40,
        "batch_defaults": {
            "target_organism": {"name": "Synthetic test virus"},
            "hosts": [{"name": "pig"}],
        },
    }


class ScientificArtifactTests(unittest.TestCase):
    def test_m1_raw_output_is_persisted_with_schema_and_integrity_metadata(self) -> None:
        import jsonschema

        with tempfile.TemporaryDirectory(prefix="agentd-scientific-artifacts-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            batch = service.create_wf01_v02_batch(valid_payload())
            manifest = service.get_wf01_v02_scientific_artifacts(
                batch["batch_id"]
            )

            self.assertEqual(manifest["artifactCount"], 1)
            entry = manifest["artifacts"][0]
            self.assertEqual(entry["stage"], "M1")
            self.assertEqual(entry["tools"], ["WF01_PREFLIGHT"])
            self.assertRegex(entry["artifactId"], r"^m1-batch-[a-f0-9]{12}$")

            artifact_path = service.get_wf01_v02_scientific_artifact_path(
                batch["batch_id"], entry["artifactId"]
            )
            content = artifact_path.read_bytes()
            document = json.loads(content)
            self.assertEqual(document["rawData"], batch["preflight"])
            self.assertEqual(entry["sizeBytes"], len(content))
            self.assertEqual(entry["sha256"], hashlib.sha256(content).hexdigest())

            artifact_schema = json.loads(
                (ROOT / "schemas" / "wf01-scientific-artifact.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            index_schema = json.loads(
                (
                    ROOT
                    / "schemas"
                    / "wf01-scientific-artifact-index.schema.json"
                ).read_text(encoding="utf-8")
            )
            jsonschema.Draft202012Validator.check_schema(artifact_schema)
            jsonschema.Draft202012Validator.check_schema(index_schema)
            jsonschema.Draft202012Validator(artifact_schema).validate(document)
            jsonschema.Draft202012Validator(index_schema).validate(manifest)

            self.assertEqual(
                service.get_wf01_v02_scientific_artifacts(
                    batch["batch_id"], stage="M2"
                )["artifactCount"],
                0,
            )


@unittest.skipUnless(
    importlib.util.find_spec("fastapi") is not None,
    "FastAPI extra not installed",
)
class ScientificArtifactApiTests(unittest.TestCase):
    def test_manifest_and_raw_json_download_endpoints(self) -> None:
        from fastapi.testclient import TestClient
        from agentd import api

        with tempfile.TemporaryDirectory(prefix="agentd-scientific-api-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            with patch.object(api, "service", service):
                client = TestClient(api.app)
                created = client.post("/v1/wf01/tasks", json=valid_payload())
                self.assertEqual(created.status_code, 200, created.text)
                batch_id = created.json()["batch_id"]

                listed = client.get(
                    f"/v1/wf01/tasks/{batch_id}/scientific-artifacts"
                )
                self.assertEqual(listed.status_code, 200, listed.text)
                self.assertEqual(listed.json()["artifactCount"], 1)
                entry = listed.json()["artifacts"][0]

                downloaded = client.get(entry["downloadUrl"])
                self.assertEqual(downloaded.status_code, 200, downloaded.text)
                self.assertEqual(
                    downloaded.json()["schemaVersion"],
                    "wf01-scientific-artifact-v1",
                )
                self.assertEqual(downloaded.json()["stage"], "M1")
                self.assertIn("rawData", downloaded.json())

                filtered = client.get(
                    f"/v1/wf01/tasks/{batch_id}/scientific-artifacts?stage=M2"
                )
                self.assertEqual(filtered.status_code, 200, filtered.text)
                self.assertEqual(filtered.json()["artifactCount"], 0)


if __name__ == "__main__":
    unittest.main()
