"""Durable execution helpers used only by the authoritative WF-02 runtime.

The public WF-02 contracts remain owned by :mod:`agentd.wf02_v01`.  This
module contains two implementation-only optimisations:

* a versioned, persistent cache for raw Primer3 heterodimer measurements; and
* a single-process background supervisor that drains persisted WF-02 units
  independently from the originating HTTP request.

Both helpers are deliberately agnostic to the scientific decision rules.  A
cache hit returns the exact raw dictionary produced by the frozen Primer3
adapter, while the background supervisor calls the existing state machine.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import queue
import sqlite3
import threading
from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger("agentd.wf02_runtime")
WF02_P4_PAIR_CACHE_VERSION = "wf02-p4-heterodimer-cache-v1"
WF02_BACKGROUND_EXECUTION_VERSION = "wf02-background-execution-v1"
WF02_BACKGROUND_ACTIVE_STATES = frozenset({"QUEUED", "RUNNING"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class WF02P4PairCache:
    """Persist exact raw heterodimer results behind a versioned cache key.

    SQLite is used only as an implementation detail.  Connections are short
    lived so test and deployment state directories can be moved or removed
    without requiring an explicit service shutdown hook.  Hot results stay in
    memory, which is the important path when thousands of panels reuse a few
    thousand unique oligonucleotide pairs.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.path = self.root / "p4-heterodimer-cache.sqlite3"
        self._memory: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._schema_ready = False
        self._hits = 0
        self._misses = 0

    def _connect(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS heterodimer_results (
                    cache_key TEXT PRIMARY KEY,
                    namespace_sha256 TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.commit()
        self._schema_ready = True

    @staticmethod
    def _cache_key(namespace: str, sequence_1: str, sequence_2: str) -> str:
        # Preserve the delegate's argument order.  Heterodimer thermodynamics
        # are expected to be symmetric, but an optimisation must not rely on
        # that assumption when exact legacy behaviour is the compatibility
        # requirement.
        sequences = [
            str(sequence_1).strip().upper(),
            str(sequence_2).strip().upper(),
        ]
        return hashlib.sha256(
            _canonical_json(
                {
                    "cache_version": WF02_P4_PAIR_CACHE_VERSION,
                    "namespace": namespace,
                    "sequences": sequences,
                }
            ).encode("utf-8")
        ).hexdigest()

    def get_or_compute(
        self,
        namespace: str,
        sequence_1: str,
        sequence_2: str,
        compute: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        cache_key = self._cache_key(namespace, sequence_1, sequence_2)
        with self._lock:
            cached = self._memory.get(cache_key)
            if cached is not None:
                self._hits += 1
                return copy.deepcopy(cached)
            try:
                self._ensure_schema()
                with closing(self._connect()) as connection:
                    row = connection.execute(
                        "SELECT result_json FROM heterodimer_results "
                        "WHERE cache_key = ?",
                        (cache_key,),
                    ).fetchone()
                if row is not None:
                    persisted = json.loads(str(row[0]))
                    if isinstance(persisted, dict):
                        self._memory[cache_key] = copy.deepcopy(persisted)
                        self._hits += 1
                        return copy.deepcopy(persisted)
            except (OSError, sqlite3.Error, json.JSONDecodeError):
                # Cache availability must never change a scientific result.
                LOGGER.exception("无法读取 WF-02 P4 序列对缓存，将重新计算")

        result = compute()
        if not isinstance(result, dict):
            raise TypeError("Primer3 heterodimer result must be a dictionary")
        result = copy.deepcopy(result)
        with self._lock:
            # Another worker may have populated the key while this call was
            # computing.  Either raw result is scientifically equivalent; the
            # first committed value is retained for deterministic persistence.
            existing = self._memory.get(cache_key)
            if existing is not None:
                self._hits += 1
                return copy.deepcopy(existing)
            self._memory[cache_key] = copy.deepcopy(result)
            self._misses += 1
            try:
                self._ensure_schema()
                namespace_sha256 = hashlib.sha256(
                    namespace.encode("utf-8")
                ).hexdigest()
                with closing(self._connect()) as connection:
                    connection.execute(
                        "INSERT OR IGNORE INTO heterodimer_results "
                        "(cache_key, namespace_sha256, result_json, created_at) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            cache_key,
                            namespace_sha256,
                            _canonical_json(result),
                            _utc_now(),
                        ),
                    )
                    connection.commit()
            except (OSError, sqlite3.Error, TypeError, ValueError):
                LOGGER.exception("无法持久化 WF-02 P4 序列对缓存")
        return copy.deepcopy(result)

    def info(self) -> dict[str, Any]:
        with self._lock:
            return {
                "version": WF02_P4_PAIR_CACHE_VERSION,
                "memory_entries": len(self._memory),
                "hits": self._hits,
                "misses": self._misses,
                "path": str(self.path),
            }


class WF02MemoizedPrimer3Adapter:
    """Transparent Primer3 proxy caching only ``calc_heterodimer`` calls."""

    def __init__(
        self,
        delegate: Any,
        cache: WF02P4PairCache,
        namespace: str,
    ) -> None:
        self._delegate = delegate
        self._cache = cache
        self._namespace = namespace

    def status(self) -> dict[str, Any]:
        return self._delegate.status()

    def calc_heterodimer(self, sequence_1: str, sequence_2: str) -> dict[str, Any]:
        return self._cache.get_or_compute(
            self._namespace,
            sequence_1,
            sequence_2,
            lambda: self._delegate.calc_heterodimer(sequence_1, sequence_2),
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


def wf02_p4_cache_namespace(
    *,
    config_fingerprint: str,
    tool_status: dict[str, Any],
    allow_fallback: bool,
) -> str:
    """Return a stable namespace covering every thermodynamic dependency."""

    return _canonical_json(
        {
            "cache_version": WF02_P4_PAIR_CACHE_VERSION,
            "config_fingerprint": config_fingerprint,
            "primer3_version": tool_status.get("version"),
            "primer3_available": tool_status.get("available"),
            "fallback_enabled": bool(allow_fallback),
            "fallback_method": tool_status.get("fallback_method"),
        }
    )


class WF02BackgroundExecutor:
    """Drain persisted WF-02 state on daemon workers after HTTP disconnects."""

    def __init__(self, workflow_service: Any, *, max_workers: int | None = None) -> None:
        self.workflow_service = workflow_service
        configured_workers = max_workers
        if configured_workers is None:
            try:
                configured_workers = int(os.environ.get("WF02_BACKGROUND_WORKERS", "1"))
            except ValueError:
                configured_workers = 1
        self.max_workers = max(1, min(int(configured_workers), 4))
        self._queue: queue.Queue[str] = queue.Queue()
        self._lock = threading.RLock()
        self._active: set[str] = set()
        self._events: dict[str, threading.Event] = {}
        self._workers_started = False

    def _ensure_workers(self) -> None:
        with self._lock:
            if self._workers_started:
                return
            for index in range(self.max_workers):
                worker = threading.Thread(
                    target=self._worker_loop,
                    name=f"wf02-background-{index + 1}",
                    daemon=True,
                )
                worker.start()
            self._workers_started = True

    def recover(self) -> None:
        """Requeue work whose persisted owner disappeared during a restart."""

        for batch_id in self.workflow_service.recoverable_background_batch_ids():
            self.start(batch_id, recovered=True)

    def start(self, batch_id: str, *, recovered: bool = False) -> threading.Event:
        batch_id = str(batch_id)
        # An already-running worker may hold the task's scientific execution
        # lock for longer than the caller's synchronous wait window.  Return
        # its event before performing any task read that would wait on that
        # lock.
        with self._lock:
            existing = self._events.get(batch_id)
            if batch_id in self._active and existing is not None:
                return existing
        batch = self.workflow_service.get_persisted_background_snapshot(batch_id)
        if self.workflow_service.is_terminal_batch(batch):
            event = threading.Event()
            event.set()
            return event
        with self._lock:
            existing = self._events.get(batch_id)
            if batch_id in self._active and existing is not None:
                return existing
            event = threading.Event()
            self._events[batch_id] = event
            self._active.add(batch_id)
            self.workflow_service.set_background_execution(
                batch_id,
                "QUEUED",
                recovered=bool(recovered),
                clear_error=not recovered,
            )
            self._ensure_workers()
            self._queue.put(batch_id)
            return event

    def wait(self, batch_id: str, timeout_seconds: float) -> bool:
        event = self.start(batch_id)
        return event.wait(max(0.0, float(timeout_seconds)))

    def is_active(self, batch_id: str) -> bool:
        with self._lock:
            return str(batch_id) in self._active

    def _worker_loop(self) -> None:
        while True:
            batch_id = self._queue.get()
            event: threading.Event | None = None
            try:
                self.workflow_service.set_background_execution(
                    batch_id,
                    "RUNNING",
                    increment_attempt=True,
                    clear_error=True,
                )
                summary = self._drain(batch_id)
                state = self.workflow_service.background_state_for_summary(summary)
                self.workflow_service.set_background_execution(batch_id, state)
            except Exception as exc:  # pragma: no cover - defensive worker boundary
                LOGGER.exception(
                    "WF-02 后台任务执行失败", extra={"batch_id": batch_id}
                )
                self.workflow_service.set_background_execution(
                    batch_id,
                    "FAILED",
                    error={
                        "code": "WF02_BACKGROUND_EXECUTION_FAILED",
                        "message": str(exc),
                        "exception_type": type(exc).__name__,
                    },
                )
            finally:
                with self._lock:
                    self._active.discard(batch_id)
                    event = self._events.pop(batch_id, None)
                if event is not None:
                    event.set()
                self._queue.task_done()

    def _drain(self, batch_id: str) -> dict[str, Any]:
        try:
            maximum_units = max(
                1,
                min(int(os.environ.get("WF02_BACKGROUND_MAX_UNITS", "10000")), 100000),
            )
        except ValueError:
            maximum_units = 10000
        previous_token: str | None = None
        summary: dict[str, Any] = {}
        for _ in range(maximum_units):
            summary = self.workflow_service.run_until_blocked(
                batch_id,
                bounded=False,
            )
            if self.workflow_service.is_background_boundary(summary):
                return summary
            token = str(summary.get("resume_token") or "")
            if not token or token == previous_token:
                raise RuntimeError("WF-02 后台执行未产生可持久化进展")
            previous_token = token
        raise RuntimeError("WF-02 后台执行超过最大持久化单元数")
