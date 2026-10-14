from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from agentd.wf01_runtime import WF01BackgroundExecutor, _write
from agentd.wf01_v02 import WF01V02BatchStore


class WF01RuntimeTests(unittest.TestCase):
    def make_runtime(self, directory):
        store = WF01V02BatchStore(directory)
        batch_id = str(uuid4())
        store.save({"batch_id": batch_id, "status": "QUEUED", "targets": []})
        entered, release = threading.Event(), threading.Event()
        calls = []
        service = SimpleNamespace(wf01_v02=SimpleNamespace(store=store))

        def run(stage, identity, options):
            calls.append(stage)
            batch = store.get(identity)
            batch["status"] = "RUNNING"
            store.save(batch)
            if stage == "M2":
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("test worker not released")
            batch[f"{stage.lower()}_outcome"] = "COMPLETED"
            batch["status"] = "STAGE_DONE"
            store.save(batch)

        for stage in ("M2", "M3", "M4", "M5", "M6"):
            setattr(service, f"execute_wf01_v02_{stage.lower()}",
                    lambda identity, options, stage=stage: run(stage, identity, options))
        return WF01BackgroundExecutor(service), batch_id, entered, release, calls

    def test_submission_query_and_duplicate_do_not_wait_or_rerun(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime, identity, entered, release, calls = self.make_runtime(directory)
            start = time.monotonic()
            event = runtime.start(identity)
            self.assertLess(time.monotonic() - start, 1)
            try:
                self.assertTrue(entered.wait(2))
                self.assertIs(runtime.start(identity), event)
                before = runtime.snapshot(identity)
                view = runtime.decorate({"batch_id": identity, "stage_outcomes": {}})
                self.assertEqual(view["next_action"], "WAIT_FOR_BACKGROUND")
                self.assertEqual(view["execution_progress"]["stage"], "M2")
                self.assertEqual(before, runtime.snapshot(identity))
                self.assertFalse(view["tool_control"]["call_again"])
                # A second service instance must not steal/replay live work.
                other = WF01BackgroundExecutor(runtime.service)
                other.recover()
                self.assertEqual(calls, ["M2"])
            finally:
                release.set()
                self.assertTrue(event.wait(5))
            self.assertEqual(runtime.snapshot(identity)["state"], "COMPLETED")
            self.assertEqual(calls, ["M2", "M3", "M4", "M5", "M6"])
            runtime.start(identity)
            self.assertEqual(len(calls), 5)

    def test_process_restart_replays_only_uncommitted_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime, identity, _, release, calls = self.make_runtime(directory)
            root = runtime.root / identity
            root.mkdir(parents=True)
            batch = runtime.store.get(identity)
            batch.update(m2_outcome="COMPLETED", status="STAGE_DONE")
            _write(root / "checkpoint.json", {"stage": "M3", "batch": batch})
            interrupted = dict(batch, status="RUNNING")
            runtime.store.save(interrupted)
            _write(root / "state.json", {"state": "RUNNING", "stage_options": {}})
            release.set()
            runtime.recover()
            runtime._queue.join()
            self.assertEqual(calls, ["M3", "M4", "M5", "M6"])
            self.assertTrue(runtime.snapshot(identity)["recovered"])
            self.assertEqual(runtime.snapshot(identity)["state"], "COMPLETED")

    def test_shared_scientific_gate_keeps_other_workflow_queued(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime, identity, entered, release, calls = self.make_runtime(directory)
            gate = threading.RLock()
            runtime._execution_lock = gate
            with gate:
                event = runtime.start(identity)
                self.assertFalse(entered.wait(0.05))
                self.assertEqual(runtime.snapshot(identity)["state"], "QUEUED")
                self.assertEqual(calls, [])
                view = runtime.decorate({"batch_id": identity})
                self.assertTrue(view["orchestration"]["background_continues"])
            try:
                self.assertTrue(entered.wait(2))
            finally:
                release.set()
                self.assertTrue(event.wait(5))

    def test_failure_is_visible_and_query_does_not_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime, identity, _, _, calls = self.make_runtime(directory)
            runtime.service.execute_wf01_v02_m2 = Mock(side_effect=RuntimeError("private details"))
            self.assertTrue(runtime.start(identity).wait(3))
            view = runtime.decorate({"batch_id": identity})
            self.assertEqual(view["background_execution"]["state"], "FAILED")
            self.assertNotIn("private details", str(view))
            runtime.service.execute_wf01_v02_m2.assert_called_once()
            self.assertTrue((runtime.root / identity / "checkpoint.json").exists())

    def test_finished_stage_commit_survives_crash_before_checkpoint_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime, identity, _, release, calls = self.make_runtime(directory)
            root = runtime.root / identity
            root.mkdir(parents=True)
            checkpoint = runtime.store.get(identity)
            _write(root / "checkpoint.json", {"stage": "M2", "batch": checkpoint})
            runtime.store.save(dict(checkpoint, m2_outcome="COMPLETED", status="STAGE_DONE"))
            _write(root / "state.json", {"state": "RUNNING", "stage_options": {}})
            release.set()
            runtime.recover()
            runtime._queue.join()
            self.assertEqual(calls, ["M3", "M4", "M5", "M6"])
