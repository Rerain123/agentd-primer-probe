from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.service import (
    AgentDService,
    WF02WF01ExecutionSnapshotV1Gateway,
    WF03InternalControl as RoutedWF03,
    WF04V01Validation as RoutedWF04,
    WF05VariationToleranceV01 as RoutedWF05,
)
from agentd.specificity import P2SpecificityBatchService
from agentd.workflows.wf03 import WF03InternalControl as MutableWF03
from agentd.workflows.wf04_v01 import WF04V01Validation as MutableWF04
from agentd_workflow_snapshots.wf02_wf01_v1.agentd.database import (
    DatabaseCatalog as SnapshotDatabaseCatalog,
)
from agentd_workflow_snapshots.wf02_wf01_v1.agentd.specificity import (
    P2_POLICY_VERSION as SNAPSHOT_P2_POLICY_VERSION,
    P2SpecificityBatchService as SnapshotP2Service,
)
from agentd_workflow_snapshots.wf02_wf01_v1.agentd.wf01_v02 import (
    WF01V02BatchService as SnapshotWF01Service,
)
from agentd_workflow_snapshots.wf02_wf01_v1.agentd.wf01_v02_m4 import (
    P2SpecificityBatchService as SnapshotM4P2Service,
)


class WorkflowIsolationTests(unittest.TestCase):
    def test_wf02_uses_complete_dedicated_wf01_package(self) -> None:
        with tempfile.TemporaryDirectory(prefix="workflow-isolation-") as state_dir:
            service = AgentDService(state_dir=state_dir)

            self.assertIsInstance(
                service.wf02_v01.gateway,
                WF02WF01ExecutionSnapshotV1Gateway,
            )
            self.assertIsInstance(service.wf02_wf01_v1, SnapshotWF01Service)
            self.assertIsNot(service.wf02_wf01_v1, service.wf01_v02)
            self.assertIsInstance(
                service.wf02_wf01_v1.catalog,
                SnapshotDatabaseCatalog,
            )
            self.assertTrue(
                type(service.wf02_wf01_v1).__module__.startswith(
                    "agentd_workflow_snapshots.wf02_wf01_v1."
                )
            )
            self.assertNotEqual(
                service.wf02_wf01_v1.store.root,
                service.wf01_v02.store.root,
            )
            manifest = service.wf02_wf01_gateway_v1.dependency_contracts()
            self.assertEqual(
                manifest["wf01_execution"]["source_release"],
                "1.1.10",
            )
            self.assertIn(
                "FULL_WF01_PYTHON_PACKAGE",
                manifest["wf01_execution"]["isolated_components"],
            )
            self.assertEqual(
                manifest["wf01_execution"]["p2_policy_version"],
                "p2-specificity-contract-v5",
            )

    def test_snapshot_m4_is_bound_to_snapshot_p2(self) -> None:
        self.assertEqual(
            SNAPSHOT_P2_POLICY_VERSION,
            "p2-specificity-contract-v5",
        )
        self.assertIs(SnapshotM4P2Service, SnapshotP2Service)
        self.assertIsNot(SnapshotM4P2Service, P2SpecificityBatchService)

    def test_wf03_wf04_and_wf05_route_to_separate_version_packages(self) -> None:
        with tempfile.TemporaryDirectory(prefix="workflow-isolation-") as state_dir:
            service = AgentDService(state_dir=state_dir)

            self.assertTrue(
                RoutedWF03.__module__.startswith(
                    "agentd_workflow_snapshots.wf03_v1."
                )
            )
            self.assertTrue(
                RoutedWF04.__module__.startswith(
                    "agentd_workflow_snapshots.wf04_v1."
                )
            )
            self.assertTrue(
                RoutedWF05.__module__.startswith(
                    "agentd_workflow_snapshots.wf05_v1."
                )
            )
            self.assertIsNot(RoutedWF03, MutableWF03)
            self.assertIsNot(RoutedWF04, MutableWF04)
            self.assertFalse(
                (ROOT / "src" / "agentd" / "workflows" / "wf05.py").exists()
            )
            self.assertNotEqual(
                service.wf03_store_v1.root,
                service.wf04_store_v1.root,
            )
            self.assertEqual(
                len(
                    {
                        service.wf03_store_v1.root,
                        service.wf04_store_v1.root,
                        service.wf05_store_v1.root,
                    }
                ),
                3,
            )
            self.assertTrue(
                type(service.wf03_catalog_v1).__module__.startswith(
                    "agentd_workflow_snapshots.wf03_v1."
                )
            )
            self.assertTrue(
                type(service.wf04_catalog_v1).__module__.startswith(
                    "agentd_workflow_snapshots.wf04_v1."
                )
            )
            self.assertTrue(
                type(service.wf05_catalog_v1).__module__.startswith(
                    "agentd_workflow_snapshots.wf05_v1."
                )
            )
            self.assertTrue(
                type(service.wf03_delivery_v1).__module__.startswith(
                    "agentd_workflow_snapshots.wf03_v1."
                )
            )
            self.assertTrue(
                type(service.wf04_delivery_v1).__module__.startswith(
                    "agentd_workflow_snapshots.wf04_v1."
                )
            )
            self.assertTrue(
                type(service.wf05_delivery_v1).__module__.startswith(
                    "agentd_workflow_snapshots.wf05_v1."
                )
            )

    def test_scoped_store_prefers_version_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="workflow-isolation-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            job_id = "33333333-1234-4234-9234-123456789abc"
            result = {"workflow": "WF-03", "job_id": job_id, "value": 1}

            destination = service.store.save(result)

            self.assertEqual(destination.parent, service.wf03_store_v1.root)
            self.assertEqual(service.store.get(job_id), result)
            compatibility = Path(state_dir) / f"{job_id}.json"
            self.assertTrue(compatibility.is_file())
            compatibility.write_text(
                json.dumps({**result, "value": "compatibility-copy-mutated"}),
                encoding="utf-8",
            )
            self.assertEqual(service.store.get(job_id), result)


if __name__ == "__main__":
    unittest.main()
