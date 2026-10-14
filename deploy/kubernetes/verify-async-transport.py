"""Isolated transport acceptance, using gated stand-ins for scientific work.

No biological input/output is printed. This verifies asynchronous control flow,
not scientific validity (covered by the separate full database acceptance).
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch


def main():
    with tempfile.TemporaryDirectory(prefix="oligosmith-async-transport-") as root:
        os.environ["AGENTD_STATE_DIR"] = root
        from fastapi.testclient import TestClient
        from agentd import api
        from agentd.wf02_v01 import summarize_wf02_batch
        service = api.service
        evidence = {"scope": "transport_only_gated_scientific_standins", "workflows": {}}
        wf01_id = str(uuid4())
        service.wf01_v02.store.save({
            "batch_id": wf01_id, "workflow": "WF-01", "status": "QUEUED",
            "created_at": "2026-10-13T00:00:00Z", "targets": [], "events": [],
        })
        entered1, release1 = threading.Event(), threading.Event()
        calls1 = []

        def run_stage(stage, batch_id, options):
            calls1.append(stage)
            batch = service.wf01_v02.store.get(batch_id)
            batch["status"] = "RUNNING"
            service.wf01_v02.store.save(batch)
            if stage == "M2":
                entered1.set()
                if not release1.wait(30):
                    raise RuntimeError("acceptance gate not released")
            batch.update(status="STAGE_DONE", **{stage.lower()+"_outcome": "COMPLETED"})
            service.wf01_v02.store.save(batch)

        for stage in ("M2", "M3", "M4", "M5", "M6"):
            setattr(service, "execute_wf01_v02_" + stage.lower(),
                    lambda identity, options, stage=stage: run_stage(stage, identity, options))

        wf02_id = str(uuid4())
        service.wf02_v01.store.save_batch({
            "batch_id": wf02_id, "workflow": "WF-02", "status": "READY_FOR_TARGET_DESIGN",
            "stage": "N05", "next_action": "RUN_TARGET_DESIGN", "targets": [],
            "created_at": "2026-10-13T00:00:00Z", "updated_at": "2026-10-13T00:00:00Z",
            "background_execution": {"state": "IDLE"},
        })
        entered2, release2 = threading.Event(), threading.Event()
        calls2 = []

        def wf02_unit(batch_id, **kwargs):
            with service.wf02_v01._batch_lock(batch_id):
                calls2.append("unit")
                entered2.set()
                if not release2.wait(30):
                    raise RuntimeError("acceptance gate not released")
                batch = service.wf02_v01.store.get_batch(batch_id)
                batch.update(status="FAILED_NO_COMPATIBLE_PANEL", stage="N26", next_action="COMPLETE")
                service.wf02_v01.store.save_batch(batch)
                return summarize_wf02_batch(batch)

        service.wf02_v01.run_until_blocked = wf02_unit
        try:
            for workflow, identity, entered, release, calls, executor in (
                ("wf01", wf01_id, entered1, release1, calls1, service.wf01_background),
                ("wf02", wf02_id, entered2, release2, calls2, service.wf02_background),
            ):
                route = f"/v1/{workflow}/tasks/{identity}"
                with TestClient(api.app) as submit_client:
                    started = time.monotonic()
                    submitted = submit_client.post(route + "/run-until-blocked", json={})
                    submit_seconds = time.monotonic() - started
                    assert submitted.status_code == 200, "submission failed"
                    assert submit_seconds < 2, "submission waited on scientific execution"
                # Submission client has disconnected; scientific worker still owns the task.
                assert entered.wait(3), "background worker did not start"
                with TestClient(api.app) as query_client:
                    started = time.monotonic()
                    queried = query_client.get(route + "/progress")
                    query_seconds = time.monotonic() - started
                    assert queried.status_code == 200, "progress query failed"
                    assert query_seconds < 2, "progress waited on scientific lock"
                    assert queried.json()["background_execution"]["state"] == "RUNNING"
                    duplicate = query_client.post(route + "/run-until-blocked", json={})
                    assert duplicate.status_code == 200
                    assert len(calls) == 1, "duplicate execution"
                release.set()
                executor._queue.join()
                with TestClient(api.app) as final_client:
                    result = final_client.get(route + "/progress").json()
                assert result["background_execution"]["state"] == "COMPLETED"
                evidence["workflows"][workflow] = {
                    "submission_seconds": round(submit_seconds, 4),
                    "query_while_computing_seconds": round(query_seconds, 4),
                    "continued_after_client_disconnect": True,
                    "duplicate_did_not_restart": True,
                    "terminal_state_observed": True,
                }
        finally:
            release1.set()
            release2.set()
            service.wf01_background._queue.join()
            service.wf02_background._queue.join()
        evidence["status"] = "PASS"
        if len(sys.argv) > 1:
            Path(sys.argv[1]).write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps(evidence), flush=True)


if __name__ == "__main__":
    main()
