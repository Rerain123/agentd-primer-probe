from __future__ import annotations

import copy
import itertools
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agentd.config import load_config
from agentd.service import AgentDService
from agentd.wf02_runtime import (
    WF02BackgroundExecutor,
    WF02MemoizedPrimer3Adapter,
    WF02P4PairCache,
)
from agentd.wf02_v01 import WF02V01BatchService, _product_window

from test_wf02_v01_target_design import FakeWF01Gateway, _candidate


class _CountingPrimer3:
    def __init__(self) -> None:
        self.calls = 0

    def status(self) -> dict:
        return {
            "tool": "P1_Primer3",
            "available": True,
            "version": "test",
            "fallback_enabled": False,
        }

    def calc_heterodimer(self, sequence_1: str, sequence_2: str) -> dict:
        self.calls += 1
        return {
            "tm": 12.5,
            "dg_kcal": -5.25,
            "structure_found": True,
            "sequences": sorted([sequence_1, sequence_2]),
        }


class _SlowGateway(FakeWF01Gateway):
    def check_p4_panel(self, payload: dict) -> dict:
        time.sleep(0.03)
        return super().check_p4_panel(payload)


class _LockHoldingGateway(FakeWF01Gateway):
    def __init__(self, reports: dict) -> None:
        super().__init__(reports)
        self.entered = threading.Event()
        self.release = threading.Event()
        self._blocked_once = False

    def check_p4_panel(self, payload: dict) -> dict:
        if not self._blocked_once:
            self._blocked_once = True
            self.entered.set()
            self.release.wait(2.0)
        return super().check_p4_panel(payload)


class WF02RuntimeTests(unittest.TestCase):
    @staticmethod
    def _payload() -> dict:
        return {
            "task_name": "WF-02 runtime regression",
            "targets": [
                {"target_id": "A", "display_name": "A", "sequence": "ACGT" * 40},
                {"target_id": "B", "display_name": "B", "sequence": "TGCA" * 40},
            ],
            "host_species": "test-host",
            "include_internal_control": False,
            "runtime_config": {"instrument": "CFX96"},
        }

    def test_product_window_matches_itertools_product_without_prefix_replay(self) -> None:
        pools = [
            [{"id": "A1"}, {"id": "A2"}],
            [{"id": "B1"}, {"id": "B2"}, {"id": "B3"}],
            [{"id": "C1"}, {"id": "C2"}],
        ]
        expected = list(itertools.product(*pools))
        for start in range(len(expected) + 1):
            for stop in range(start, len(expected) + 1):
                self.assertEqual(
                    _product_window(pools, start, stop),
                    expected[start:stop],
                )

    def test_pair_cache_is_order_exact_persistent_and_namespace_versioned(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf02-p4-cache-") as state_dir:
            first_delegate = _CountingPrimer3()
            first_cache = WF02P4PairCache(state_dir)
            first = WF02MemoizedPrimer3Adapter(
                first_delegate,
                first_cache,
                "namespace-v1",
            )
            expected = first.calc_heterodimer("AAAAAA", "CCCCCC")
            reversed_result = first.calc_heterodimer("CCCCCC", "AAAAAA")
            self.assertEqual(reversed_result, expected)
            self.assertEqual(first_delegate.calls, 2)

            second_delegate = _CountingPrimer3()
            second = WF02MemoizedPrimer3Adapter(
                second_delegate,
                WF02P4PairCache(state_dir),
                "namespace-v1",
            )
            self.assertEqual(
                second.calc_heterodimer("AAAAAA", "CCCCCC"),
                expected,
            )
            self.assertEqual(second_delegate.calls, 0)

            changed_namespace = WF02MemoizedPrimer3Adapter(
                second_delegate,
                WF02P4PairCache(state_dir),
                "namespace-v2",
            )
            self.assertEqual(
                changed_namespace.calc_heterodimer("AAAAAA", "CCCCCC"),
                expected,
            )
            self.assertEqual(second_delegate.calls, 1)

    def test_scoped_create_retry_reuses_recent_task_without_storing_identity(self) -> None:
        gateway = FakeWF01Gateway({})
        with tempfile.TemporaryDirectory(prefix="wf02-create-replay-") as state_dir:
            service = WF02V01BatchService(
                state_dir,
                load_config(),
                gateway=gateway,
            )
            first = service.create_batch(
                self._payload(),
                creation_scope="sub:test-caller",
            )
            replayed = service.create_batch(
                self._payload(),
                creation_scope="sub:test-caller",
            )
            other_caller = service.create_batch(
                self._payload(),
                creation_scope="sub:other-caller",
            )

            self.assertEqual(replayed["batch_id"], first["batch_id"])
            self.assertTrue(replayed["creation_replayed"])
            self.assertNotEqual(other_caller["batch_id"], first["batch_id"])
            self.assertNotIn("test-caller", str(first))
            self.assertRegex(
                first["creation_request"]["request_sha256"],
                r"^[0-9a-f]{64}$",
            )

    def test_background_timeout_stops_waiting_but_task_reaches_terminal_state(self) -> None:
        candidates_a = [
            _candidate(1, amplicon_sequence="ACGT" * 20),
            _candidate(2, amplicon_sequence="ACGT" * 21),
        ]
        candidates_b = [
            _candidate(1, amplicon_sequence="ACGT" * 30),
            _candidate(2, amplicon_sequence="ACGT" * 31),
        ]
        gateway = _SlowGateway(
            {
                "A": {"candidates": copy.deepcopy(candidates_a)},
                "B": {"candidates": copy.deepcopy(candidates_b)},
            }
        )
        payload = {
            "task_name": "WF-02 background test",
            "targets": [
                {"target_id": "A", "display_name": "A", "sequence": "ACGT" * 40},
                {"target_id": "B", "display_name": "B", "sequence": "TGCA" * 40},
            ],
            "host_species": "猪",
            "include_internal_control": False,
            "runtime_config": {"instrument": "CFX96"},
        }
        with tempfile.TemporaryDirectory(prefix="wf02-background-") as state_dir:
            service = WF02V01BatchService(
                state_dir,
                load_config(),
                gateway=gateway,
            )
            created = service.create_batch(payload)
            executor = WF02BackgroundExecutor(service)
            event = executor.start(created["batch_id"])
            self.assertFalse(event.wait(0.001))

            running = service.get_batch(created["batch_id"])
            self.assertIn(
                running["background_execution"]["state"],
                {"QUEUED", "RUNNING"},
            )
            self.assertTrue(event.wait(10.0))

            completed = service.get_batch(created["batch_id"])
            self.assertEqual(completed["status"], "COMPLETED")
            self.assertEqual(
                completed["background_execution"]["state"],
                "COMPLETED",
            )
            self.assertEqual(len(gateway.p4_calls), 4)

    def test_sync_deadline_returns_while_worker_holds_task_lock(self) -> None:
        gateway = _LockHoldingGateway(
            {
                "A": {"candidates": [_candidate(1, amplicon_sequence="ACGT" * 20)]},
                "B": {"candidates": [_candidate(1, amplicon_sequence="ACGT" * 30)]},
            }
        )
        with tempfile.TemporaryDirectory(prefix="wf02-lock-deadline-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            service.wf02_v01.gateway = gateway
            created = service.wf02_v01.create_batch(self._payload())
            completion = service.wf02_background.start(created["batch_id"])
            self.assertTrue(gateway.entered.wait(2.0))

            timer = threading.Timer(0.75, gateway.release.set)
            timer.start()
            started = time.monotonic()
            try:
                waiting = service.run_wf02_v01_background(
                    created["batch_id"],
                    wait_seconds=0.05,
                )
                with patch.object(service.wf02_v01, "get_batch", side_effect=AssertionError("blocking read")):
                    queried = service.get_wf02_v01_agent_summary(created["batch_id"])
                self.assertEqual(queried["background_execution"]["state"], "RUNNING")
            finally:
                gateway.release.set()
                timer.cancel()
            elapsed = time.monotonic() - started

            self.assertLess(elapsed, 0.4)
            self.assertEqual(
                waiting["flowControl"]["action"],
                "WAIT_FOR_BACKGROUND",
            )
            self.assertEqual(waiting["background_execution"]["state"], "RUNNING")
            self.assertTrue(waiting["orchestration"]["background_continues"])
            self.assertTrue(completion.wait(10.0))

    def test_background_and_foreground_paths_have_identical_scientific_outputs(self) -> None:
        candidate_sets = {
            "A": {
                "candidates": [
                    _candidate(1, amplicon_sequence="ACGT" * 20),
                    _candidate(2, amplicon_sequence="ACGT" * 21),
                ]
            },
            "B": {
                "candidates": [
                    _candidate(1, amplicon_sequence="ACGT" * 30),
                    _candidate(2, amplicon_sequence="ACGT" * 31),
                ]
            },
        }
        config = load_config(
            overrides={"multiplex": {"maximum_panel_combinations": 2}}
        )
        with tempfile.TemporaryDirectory(
            prefix="wf02-foreground-reference-"
        ) as foreground_dir, tempfile.TemporaryDirectory(
            prefix="wf02-background-candidate-"
        ) as background_dir:
            foreground_gateway = FakeWF01Gateway(copy.deepcopy(candidate_sets))
            foreground = WF02V01BatchService(
                foreground_dir,
                config,
                gateway=foreground_gateway,
            )
            foreground_created = foreground.create_batch(self._payload())
            for _ in range(50):
                foreground_summary = foreground.run_until_blocked(
                    foreground_created["batch_id"],
                    bounded=False,
                )
                if foreground.is_terminal_batch(
                    foreground.get_batch(foreground_created["batch_id"])
                ):
                    break
            else:  # pragma: no cover - defensive test boundary
                self.fail("foreground reference did not terminate")

            background_gateway = FakeWF01Gateway(copy.deepcopy(candidate_sets))
            background = WF02V01BatchService(
                background_dir,
                config,
                gateway=background_gateway,
            )
            background_created = background.create_batch(self._payload())
            event = WF02BackgroundExecutor(background).start(
                background_created["batch_id"]
            )
            self.assertTrue(event.wait(10.0))

            foreground_batch = foreground.get_batch(foreground_created["batch_id"])
            background_batch = background.get_batch(background_created["batch_id"])
            self.assertEqual(background_batch["status"], foreground_batch["status"])
            self.assertEqual(background_batch["stage"], foreground_batch["stage"])
            self.assertEqual(background_batch["outcome"], foreground_batch["outcome"])
            self.assertEqual(background_gateway.p4_calls, foreground_gateway.p4_calls)

            foreground_screening = foreground_batch["multiplex"]["screening"]
            background_screening = background_batch["multiplex"]["screening"]
            for key in (
                "total_combinations",
                "cursor",
                "strict_count",
                "risk_count",
                "channel_assignment",
                "panels",
            ):
                self.assertEqual(background_screening[key], foreground_screening[key])

            foreground_report = foreground.get_report(foreground_created["batch_id"])
            background_report = background.get_report(background_created["batch_id"])
            for key in (
                "selection_mode",
                "result_summary",
                "candidate_comparison",
                "result_explanation",
                "top3_panels",
            ):
                self.assertEqual(background_report[key], foreground_report[key])

    def test_stale_resume_token_is_absorbed_without_a_model_retry_loop(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [_candidate(1, amplicon_sequence="ACGT" * 20)]},
                "B": {"candidates": [_candidate(1, amplicon_sequence="ACGT" * 30)]},
            }
        )
        with tempfile.TemporaryDirectory(prefix="wf02-stale-background-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            service.wf02_v01.gateway = gateway
            created = service.wf02_v01.create_batch(self._payload())
            completed = service.run_wf02_v01_background(
                created["batch_id"],
                wait_seconds=10.0,
                expected_resume_token="stale-client-token",
            )
            self.assertTrue(
                completed["orchestration"]["stale_resume_token_recovered"]
            )
            self.assertEqual(
                completed["execution_unit"]["status"],
                "STALE_RESUME_TOKEN_RECOVERED",
            )
            self.assertEqual(
                completed["background_execution"]["state"],
                "COMPLETED",
            )

    def test_recovery_requeues_a_persisted_active_task(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [_candidate(1, amplicon_sequence="ACGT" * 20)]},
                "B": {"candidates": [_candidate(1, amplicon_sequence="ACGT" * 30)]},
            }
        )
        payload = {
            "targets": [
                {"target_id": "A", "display_name": "A", "sequence": "ACGT" * 40},
                {"target_id": "B", "display_name": "B", "sequence": "TGCA" * 40},
            ],
            "host_species": "猪",
            "include_internal_control": False,
            "runtime_config": {"instrument": "CFX96"},
        }
        with tempfile.TemporaryDirectory(prefix="wf02-recovery-") as state_dir:
            service = WF02V01BatchService(
                state_dir,
                load_config(),
                gateway=gateway,
            )
            created = service.create_batch(payload)
            service.set_background_execution(created["batch_id"], "RUNNING")

            recovered = WF02BackgroundExecutor(service)
            recovered.recover()
            event = recovered.start(created["batch_id"])
            self.assertTrue(event.wait(10.0))
            completed = service.get_batch(created["batch_id"])
            self.assertEqual(completed["status"], "COMPLETED")
            self.assertTrue(completed["background_execution"]["recovered"])


if __name__ == "__main__":
    unittest.main()
