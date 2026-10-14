"""Durable WF-01 submission, independent of the lifetime of an MCP request."""

from __future__ import annotations

import copy
import json
import os
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import InputValidationError
from .wf01_agent_view import next_action_for_batch, next_stage_for_batch


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _acquire(path: Path):
    """Keep a nonblocking OS lease across queued and running work."""
    stream = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            if path.stat().st_size == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        return None
    return stream


class WF01BackgroundExecutor:
    """One scientific worker; durable intents and per-stage recovery checkpoints.

    Scientific snapshots and transport state have separate writers. An OS lease
    prevents another service instance from replaying live work. On process loss,
    only an unfinished stage is replayed from its pre-stage checkpoint.
    """

    def __init__(self, service: Any) -> None:
        self.service = service
        self.store = service.wf01_v02.store
        self.root = self.store.root.parent / "wf01-background"
        self._execution_lock = getattr(service, "_workflow_execution_lock", threading.RLock())
        self._lock = threading.RLock()
        self._queue: queue.Queue = queue.Queue()
        self._events: dict[str, threading.Event] = {}
        self._started = False

    def snapshot(self, batch_id: str) -> dict:
        path = self.root / batch_id / "state.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def recover(self) -> None:
        for path in self.root.glob("*/state.json"):
            state = json.loads(path.read_text(encoding="utf-8"))
            if state.get("state") in {"QUEUED", "RUNNING"}:
                self.start(path.parent.name, recovered=True)

    @staticmethod
    def validate_options(options: dict | None) -> dict:
        if options is None:
            return {}
        if not isinstance(options, dict):
            raise InputValidationError("stage_options 必须为对象")
        normalized = {}
        for key, value in options.items():
            stage = str(key).upper()
            if stage not in {"M2", "M3", "M4", "M5", "M6"} or not isinstance(value, dict):
                raise InputValidationError("stage_options 必须使用 M2–M6 阶段及对象参数")
            normalized[stage] = copy.deepcopy(value)
        return normalized

    def start(self, reference: str, options: dict | None = None, *, recovered=False):
        normalized = self.validate_options(options)
        batch = self.store.get(reference)
        batch_id = str(batch["batch_id"])
        with self._lock:
            if batch_id in self._events:
                return self._events[batch_id]
            event = threading.Event()
            if batch.get("m6_outcome") == "COMPLETED":
                event.set()
                return event
            directory = self.root / batch_id
            directory.mkdir(parents=True, exist_ok=True)
            lease = _acquire(directory / "owner.lock")
            if lease is None:
                return event  # Another process owns this work; never enqueue twice.
            try:
                state = self.snapshot(batch_id)
                state.update(
                    state="QUEUED", updated_at=_now(), error=None,
                    recovered=bool(recovered),
                    stage_options=normalized if options is not None else state.get("stage_options", {}),
                )
                state.setdefault("submitted_at", _now())
                _write(directory / "state.json", state)
                self._events[batch_id] = event
                if not self._started:
                    threading.Thread(target=self._worker, name="wf01-background", daemon=True).start()
                    self._started = True
                self._queue.put((batch_id, lease, event))
            except Exception:
                lease.close()
                raise
            return event

    def _worker(self) -> None:
        while True:
            batch_id, lease, event = self._queue.get()
            try:
                with self._execution_lock:
                    self._drain(batch_id)
            except Exception as exc:
                state = self.snapshot(batch_id)
                state.update(state="FAILED", updated_at=_now(), error={
                    "code": "WF01_BACKGROUND_EXECUTION_FAILED",
                    "exception_type": type(exc).__name__,
                })
                _write(self.root / batch_id / "state.json", state)
            finally:
                with self._lock:
                    lease.close()
                    self._events.pop(batch_id, None)
                    event.set()
                self._queue.task_done()

    def _drain(self, batch_id: str) -> None:
        directory = self.root / batch_id
        state = self.snapshot(batch_id)
        checkpoint_path = directory / "checkpoint.json"
        if checkpoint_path.exists():
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            current = self.store.get(batch_id)
            # A crash after the scientific commit must not replay a finished stage.
            if current.get(f"{checkpoint['stage'].lower()}_outcome") is None:
                self.store.save(checkpoint["batch"])
            checkpoint_path.unlink()
        state.update(state="RUNNING", updated_at=_now())
        _write(directory / "state.json", state)
        while True:
            batch = self.store.get(batch_id)
            stage = next_stage_for_batch(batch)
            if stage is None:
                action = next_action_for_batch(batch)
                state.update(
                    state="COMPLETED" if action == "COMPLETE" else
                    "WAITING_USER" if action == "WAIT_FOR_USER" else "INTERRUPTED",
                    active_stage=None, updated_at=_now(),
                )
                _write(directory / "state.json", state)
                return
            _write(checkpoint_path, {"stage": stage, "batch": batch})
            state.update(active_stage=stage, updated_at=_now())
            _write(directory / "state.json", state)
            runner = getattr(self.service, f"execute_wf01_v02_{stage.lower()}")
            runner(batch_id, state["stage_options"].get(stage, {}))
            if self.store.get(batch_id).get(f"{stage.lower()}_outcome") is None:
                raise RuntimeError("WF-01 stage did not commit an outcome")
            checkpoint_path.unlink()

    def decorate(self, summary: dict) -> dict:
        state = self.snapshot(str(summary["batch_id"]))
        if not state:
            return summary
        public = {key: value for key, value in state.items() if key != "stage_options"}
        terminal = (summary.get("stage_outcomes") or {}).get("M6") == "COMPLETED"
        if terminal:
            public.update(state="COMPLETED", active_stage=None, error=None)
        active = not terminal and state.get("state") in {"QUEUED", "RUNNING"}
        summary["background_execution"] = public
        summary["execution_progress"] = {
            "stage": public.get("active_stage"),
            "completed_stage_count": len(summary.get("stage_outcomes") or {}),
            "total_stage_count": 5,
            "updated_at": state.get("updated_at"),
        }
        summary["orchestration"] = {"mode": "PERSISTENT_BACKGROUND", "background_continues": active}
        summary["tool_control"] = {
            "mode": "PERSISTENT_BACKGROUND", "directive": "DELIVER_TERMINAL_OUTPUT" if terminal else "STOP_CURRENT_TURN",
            "call_again": False, "continue_in_same_turn": False,
            "next_tool": None, "next_arguments": None,
        }
        if active:
            summary["next_action"] = "WAIT_FOR_BACKGROUND"
            summary["flowControl"] = {"action": "WAIT_FOR_BACKGROUND", "stopCurrentTurn": True, "retryInCurrentTurn": False}
            summary["user_message"] = (
                f"任务 {summary.get('batch_code') or summary['batch_id']} 已在后台运行。"
                f"当前：{state.get('active_stage') or '排队中'}；可关闭连接，稍后输入“查询进度”。"
            )
        elif not terminal and state.get("state") in {"FAILED", "INTERRUPTED"}:
            summary["next_action"] = "RETRY_BACKGROUND"
            summary["user_message"] = "后台任务中断，已保留阶段记录；查询不会自动重试，请明确选择继续。"
        return summary
