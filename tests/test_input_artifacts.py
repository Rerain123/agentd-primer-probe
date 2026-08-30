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
            "https://himat.ws.scqcyz.cn/api/files/download/42"
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

            wf03_payload = store.materialize_wf03_payload(
                {
                    "host_species": "pig",
                    "input_artifact_id": artifact["input_artifact_id"],
                    "input_gene": "CUSTOM_INPUT",
                }
            )
            wf03_path = Path(wf03_payload["gene_fasta_paths"]["CUSTOM_INPUT"])
            self.assertTrue(wf03_path.is_file())
            self.assertEqual(wf03_path.read_bytes(), FASTA)
            self.assertEqual(wf03_payload["preferred_genes"], ["CUSTOM_INPUT"])
            self.assertEqual(
                wf03_payload["_wf03_input_artifact"]["input_artifact_id"],
                artifact["input_artifact_id"],
            )
            self.assertNotIn("input_artifact_id", wf03_payload)
            self.assertNotIn("input_gene", wf03_payload)

            wf05_payload = store.materialize_wf05_payload(
                {"input_artifact_id": artifact["input_artifact_id"]}
            )
            self.assertTrue(Path(wf05_payload["fasta_path"]).is_file())
            self.assertEqual(
                wf05_payload["_wf05_input_artifact"]["input_artifact_id"],
                artifact["input_artifact_id"],
            )
            self.assertNotIn("input_artifact_id", wf05_payload)

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

    def test_wf04_artifact_maps_named_records_inside_backend(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf04-input-") as state_dir:
            store = HiMatInputArtifactStore(state_dir)
            artifact = store.store_inline_fasta(
                "\n".join(
                    (
                        ">forward",
                        "ACGTACGTACGTACGTACGA",
                        ">reverse",
                        "TCGTACGTACGTACGTACGT",
                        ">probe",
                        "CACGTACGTACGTACGTACG",
                        ">template",
                        "ACGT" * 40,
                    )
                )
                + "\n",
                file_name="wf04-input.fasta",
            )

            payload = store.materialize_wf04_payload(
                {"input_artifact_id": artifact["input_artifact_id"]}
            )

            self.assertEqual(payload["forward_primer"], "ACGTACGTACGTACGTACGA")
            self.assertEqual(payload["reverse_primer"], "TCGTACGTACGTACGTACGT")
            self.assertEqual(payload["probe"], "CACGTACGTACGTACGTACG")
            self.assertEqual(payload["template"], "ACGT" * 40)
            self.assertEqual(
                payload["_wf04_input_artifact"]["mapped_fields"],
                ["forward_primer", "probe", "reverse_primer", "template"],
            )
            self.assertNotIn("input_artifact_id", payload)

    def test_wf04_single_upload_supplies_template_for_inline_pair(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf04-template-") as state_dir:
            store = HiMatInputArtifactStore(state_dir)
            artifact = store.store_inline_fasta(
                ">uploaded\n" + "ACGT" * 40 + "\n",
                file_name="wf04-template.fasta",
            )

            payload = store.materialize_wf04_payload(
                {
                    "forward_primer": "ACGTACGTACGTACGTACGA",
                    "reverse_primer": "TCGTACGTACGTACGTACGT",
                    "input_artifact_id": artifact["input_artifact_id"],
                }
            )

            self.assertEqual(payload["template"], "ACGT" * 40)

    def test_wf04_mixed_named_and_unnamed_records_map_missing_core(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf04-mixed-input-") as state_dir:
            store = HiMatInputArtifactStore(state_dir)
            artifact = store.store_inline_fasta(
                "\n".join(
                    (
                        ">forward",
                        "ACGTACGTACGTACGTACGA",
                        ">uploaded-record",
                        "TCGTACGTACGTACGTACGT",
                        ">probe",
                        "CACGTACGTACGTACGTACG",
                    )
                )
                + "\n",
                file_name="wf04-mixed-input.fasta",
            )

            payload = store.materialize_wf04_payload(
                {"input_artifact_id": artifact["input_artifact_id"]}
            )

            self.assertEqual(payload["forward_primer"], "ACGTACGTACGTACGTACGA")
            self.assertEqual(payload["reverse_primer"], "TCGTACGTACGTACGTACGT")
            self.assertEqual(payload["probe"], "CACGTACGTACGTACGTACG")

    def test_wf04_ambiguous_upload_is_rejected_in_backend(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf04-ambiguous-") as state_dir:
            store = HiMatInputArtifactStore(state_dir)
            artifact = store.store_inline_fasta(
                "\n".join(
                    (
                        ">forward",
                        "ACGTACGTACGTACGTACGA",
                        ">record-one",
                        "TCGTACGTACGTACGTACGT",
                        ">record-two",
                        "CACGTACGTACGTACGTACG",
                    )
                )
                + "\n",
            )

            with self.assertRaisesRegex(InputValidationError, "无法确定性映射"):
                store.materialize_wf04_payload(
                    {"input_artifact_id": artifact["input_artifact_id"]}
                )

    def test_wf04_upload_inline_conflict_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf04-conflict-") as state_dir:
            store = HiMatInputArtifactStore(state_dir)
            artifact = store.store_inline_fasta(
                "\n".join(
                    (
                        ">forward",
                        "ACGTACGTACGTACGTACGA",
                        ">reverse",
                        "TCGTACGTACGTACGTACGT",
                    )
                )
                + "\n",
            )

            with self.assertRaisesRegex(InputValidationError, "内联字段冲突"):
                store.materialize_wf04_payload(
                    {
                        "input_artifact_id": artifact["input_artifact_id"],
                        "forward_primer": "AAAAAAAAAAAAAAAAAAAA",
                    }
                )

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

    def test_service_create_accepts_raw_pasted_sequence_without_form(self) -> None:
        with tempfile.TemporaryDirectory(prefix="inline-create-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            raw_sequence = "ACGT" * 40

            batch = service.create_wf01_v02_batch(
                {
                    "sequence": raw_sequence,
                    "target_organism": "测试病毒",
                    "host": "猪",
                },
                interaction_mode="NO_POPUPS",
            )

            self.assertEqual(batch["status"], "QUEUED")
            self.assertEqual(
                batch["target_manifest"]["targets"][0]["execution_path"],
                "FULL_SPECIFICITY",
            )
            self.assertEqual(batch["targets"][0]["template"]["length_nt"], 160)

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
            with self.assertRaises(InputValidationError):
                store.materialize_wf03_payload({"input_gene": "CUSTOM_INPUT"})
            with self.assertRaises(InputValidationError):
                store.materialize_wf05_payload(
                    {
                        "input_artifact_id": "himat-" + "0" * 32,
                        "sequences": ["ACGT", "ACGT"],
                    }
                )
            for unsupported in ("fasta_path", "_wf05_retry_context", "extra"):
                with self.subTest(unsupported=unsupported):
                    with self.assertRaisesRegex(
                        InputValidationError,
                        "未声明字段",
                    ):
                        store.materialize_wf05_payload(
                            {
                                "sequences": ["ACGT", "ACGT"],
                                unsupported: "not-accepted",
                            }
                        )
            invalid_payloads = (
                {"sequences": ["ACGT", "ACGT"], "allow_fallback": None},
                {"sequences": ["ACGT"]},
                {"sequences": ["ACGT", {"sequence": "ACGT", "extra": 1}]},
                {"sequences": ["ACGT", {"sequence": "ACGT", "id": 2}]},
                {"sequences": ["ACGT", "ACGT"], "target_id": ""},
            )
            for payload in invalid_payloads:
                with self.subTest(payload=payload):
                    with self.assertRaises(InputValidationError):
                        store.materialize_wf05_payload(payload)


if __name__ == "__main__":
    unittest.main()
