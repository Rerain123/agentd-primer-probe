from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.errors import InputValidationError
from agentd.input_artifacts import HiMatInputArtifactStore
from agentd.service import AgentDService


FASTA = b">target_001\n" + b"ACGT" * 40 + b"\n"


class FakeResponse:
    def __init__(self, body: bytes, headers: dict[str, str]) -> None:
        self._body = io.BytesIO(body)
        self.headers = headers

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def fake_opener(body: bytes = FASTA):
    def open_request(request, *, timeout: float):
        assert request.full_url == (
            "https://himat.ne.scqcyz.cn/api/files/download/42"
        )
        assert timeout == 30
        return FakeResponse(
            body,
            {
                "content-disposition": (
                    "attachment; filename*=UTF-8''CSFV_%E7%9B%AE%E6%A0%87.fasta"
                ),
                "content-type": "text/x-fasta; charset=utf-8",
            },
        )

    return open_request


class HiMatInputArtifactStoreTests(unittest.TestCase):
    def test_download_returns_metadata_without_body_or_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="himat-input-") as state_dir:
            store = HiMatInputArtifactStore(
                state_dir,
                timeout_seconds=30,
                opener=fake_opener(),
            )
            result = store.download(42)

            self.assertRegex(
                result["input_artifact_id"], r"^himat-[0-9a-f]{32}$"
            )
            self.assertEqual(result["file_name"], "CSFV_目标.fasta")
            self.assertEqual(result["content_type"], "text/x-fasta")
            self.assertEqual(result["size_bytes"], len(FASTA))
            self.assertNotIn("path", result)
            self.assertNotIn("url", result)
            self.assertNotIn("content", result)
            self.assertNotIn("fasta_text", result)

            path, manifest = store.resolve(result["input_artifact_id"])
            self.assertEqual(path.read_bytes(), FASTA)
            self.assertEqual(manifest["sha256"], result["sha256"])

    def test_artifact_materializes_only_inside_scientific_input(self) -> None:
        with tempfile.TemporaryDirectory(prefix="himat-input-") as state_dir:
            store = HiMatInputArtifactStore(
                state_dir,
                timeout_seconds=30,
                opener=fake_opener(),
            )
            artifact = store.download(42)

            wf01_payload = store.materialize_wf01_payload(
                {"input_artifact_id": artifact["input_artifact_id"]}
            )
            sequence_file = wf01_payload["sequence_files"][0]
            self.assertEqual(sequence_file["file_name"], "CSFV_目标.fasta")
            self.assertEqual(sequence_file["fasta_text"].encode("utf-8"), FASTA)
            self.assertNotIn("input_artifact_id", sequence_file)

            generic_payload = store.attach_path(
                {"input_artifact_id": artifact["input_artifact_id"]}
            )
            self.assertTrue(Path(generic_payload["fasta_path"]).is_file())
            self.assertNotIn("input_artifact_id", generic_payload)

    def test_inline_fasta_is_persisted_as_opaque_reusable_reference(self) -> None:
        with tempfile.TemporaryDirectory(prefix="inline-input-") as state_dir:
            store = HiMatInputArtifactStore(state_dir)
            text = FASTA.decode("utf-8")

            first = store.store_inline_fasta(text)
            repeated = store.store_inline_fasta(text)

            self.assertEqual(first["input_artifact_id"], repeated["input_artifact_id"])
            self.assertEqual(first["source_type"], "INLINE_SEQUENCE")
            self.assertIsNone(first["source_file_id"])
            self.assertNotIn("fasta_text", first)
            payload = store.materialize_wf01_payload(
                {"input_artifact_id": first["input_artifact_id"]}
            )
            sequence_file = payload["sequence_files"][0]
            self.assertEqual(sequence_file["fasta_text"], text)
            self.assertEqual(sequence_file["file_name"], "直接输入序列.fasta")

    def test_service_preflight_accepts_artifact_reference(self) -> None:
        with tempfile.TemporaryDirectory(prefix="himat-input-") as state_dir:
            store = HiMatInputArtifactStore(
                state_dir,
                timeout_seconds=30,
                opener=fake_opener(),
            )
            artifact = store.download(42)
            service = AgentDService(
                state_dir=state_dir,
                input_artifact_store=store,
            )

            payload = {
                "input_artifact_id": artifact["input_artifact_id"],
                "batch_defaults": {
                    "target_organism": {"name": "猪瘟病毒"},
                    "hosts": [{"name": "猪"}],
                    "coverage_requirement": "覆盖目标序列",
                },
            }
            result = service.preflight_wf01_v02(payload)

            self.assertTrue(result["can_auto_run"])
            self.assertEqual(result["targets"][0]["template"]["length_nt"], 160)
            batch = service.create_wf01_v02_batch(payload)
            self.assertEqual(batch["status"], "QUEUED")
            self.assertEqual(batch["targets"][0]["template"]["length_nt"], 160)

    def test_service_prepare_wraps_raw_pasted_text_and_returns_reference(self) -> None:
        with tempfile.TemporaryDirectory(prefix="inline-prepare-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            raw_sequence = "ACGT" * 40

            form = service.prepare_wf01_design_input(
                {
                    "fasta_text": raw_sequence,
                    "designInput": {
                        "targetOrganism": "测试病毒",
                        "host": "猪",
                        "coverageRequirement": "单一毒株",
                    },
                }
            )

            source = form["data"]["sourceFile"]
            self.assertEqual(source["inputMode"], "INLINE_SEQUENCE")
            materialized = service.materialize_wf01_input(
                {"input_artifact_id": source["inputArtifactId"]}
            )
            fasta_text = materialized["sequence_files"][0]["fasta_text"]
            self.assertTrue(fasta_text.startswith(">pasted_sequence\n"))
            self.assertIn(raw_sequence, fasta_text)

    def test_invalid_and_conflicting_references_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="himat-input-") as state_dir:
            store = HiMatInputArtifactStore(state_dir)
            with self.assertRaises(InputValidationError):
                store.resolve("../outside")
            with self.assertRaises(InputValidationError):
                store.materialize_wf01_payload(
                    {
                        "input_artifact_id": "himat-" + "0" * 32,
                        "sequence_files": [{"fasta_text": ">x\nACGT"}],
                    }
                )


if __name__ == "__main__":
    unittest.main()
