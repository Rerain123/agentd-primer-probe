from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.service import AgentDService


class JsonSchemaContractTests(unittest.TestCase):
    def test_primer_design_form_and_user_update_have_separate_schemas(self) -> None:
        import jsonschema

        schema = json.loads(
            (ROOT / "schemas" / "primer-design-input.schema.json").read_text(
                encoding="utf-8"
            )
        )
        jsonschema.Draft202012Validator.check_schema(schema)
        message = {
            "type": "primer_design_input",
            "data": {
                "status": "WAITING_USER",
                "confirmed": False,
                "canStart": True,
                "summaryText": "猪瘟病毒（CSFV）·宿主：鸭·覆盖多个分型/毒株",
                "sourceFile": {"fileName": "CSFV_5UTR.fasta", "fileType": "FASTA"},
                "designInput": {
                    "targetOrganism": "猪瘟病毒（CSFV）",
                    "host": "鸭",
                    "coverageRequirement": "覆盖多个分型/毒株",
                },
                "issues": [],
            },
        }
        jsonschema.validate(message, schema)
        inline_message = json.loads(json.dumps(message, ensure_ascii=False))
        inline_message["data"]["sourceFile"] = {
            "fileName": "直接输入序列.fasta",
            "fileType": "FASTA",
            "inputMode": "INLINE_SEQUENCE",
            "inputArtifactId": "himat-0123456789abcdef0123456789abcdef",
        }
        jsonschema.validate(inline_message, schema)

        update_schema = json.loads(
            (ROOT / "schemas" / "primer-design-input-update.schema.json").read_text(
                encoding="utf-8"
            )
        )
        jsonschema.Draft202012Validator.check_schema(update_schema)
        jsonschema.validate(
            {
                "type": "primer_design_input_update",
                "data": {"designInput": {"host": "鸭"}},
            },
            update_schema,
        )
        jsonschema.validate(
            {
                "type": "primer_design_input_update",
                "data": {"designInput": {}},
            },
            update_schema,
        )
        jsonschema.validate(
            {
                "type": "primer_design_input_update",
                "data": {},
            },
            update_schema,
        )

    def test_published_schema_accepts_wf04_output(self) -> None:
        import jsonschema

        schema = json.loads((ROOT / "schemas" / "agentd-output.schema.json").read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        payload = json.loads((ROOT / "examples" / "wf04-request.json").read_text(encoding="utf-8"))
        payload["host_db"] = str(ROOT / payload["host_db"])
        payload["cross_db"] = str(ROOT / payload["cross_db"])
        with tempfile.TemporaryDirectory(prefix="agentd-schema-") as state_dir:
            output = AgentDService(state_dir=state_dir).run("WF-04", payload, persist=False)
        jsonschema.Draft202012Validator(schema).validate(output)


if __name__ == "__main__":
    unittest.main()
