"""Private, bounded cache for successful BLAST batches on versioned local DBs.

Only raw search evidence is reused. P2 decisions and thermodynamics run afresh.
The database must be an immutable, versioned directory while a search runs.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import tempfile
import time

from .errors import InputValidationError


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


@contextmanager
def locked(path):
    """OS locks release on process exit, including crashes; no stale leases."""
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            if stream.seek(0, 2) == 0:
                stream.write(b"0")
                stream.flush()
            while True:
                try:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


class BlastResultCache:
    def __init__(self, settings=None):
        settings = {} if settings is None else settings
        allowed = {"enabled", "directory", "max_bytes", "max_entry_bytes", "ttl_seconds"}
        if not isinstance(settings, dict) or set(settings) - allowed:
            raise InputValidationError("Invalid tools.blast_cache configuration")
        if type(settings.get("enabled", False)) is not bool:
            raise InputValidationError("blast_cache.enabled must be boolean")
        self.enabled = settings.get("enabled", False)
        directory = settings.get("directory")
        if self.enabled and (not isinstance(directory, str) or not Path(directory).is_absolute()):
            raise InputValidationError("Enabled blast_cache requires an absolute directory")
        self.root = Path(directory) if isinstance(directory, str) else None
        self.max_bytes = settings.get("max_bytes", 2 * 1024**3)
        self.max_entry_bytes = settings.get("max_entry_bytes", 128 * 1024**2)
        self.ttl = settings.get("ttl_seconds", 7 * 86400)
        if any(type(v) is not int or v <= 0 for v in (self.max_bytes, self.max_entry_bytes, self.ttl)):
            raise InputValidationError("BLAST cache limits must be positive integers")
        if self.max_entry_bytes > self.max_bytes:
            raise InputValidationError("BLAST cache entry limit exceeds total limit")

    def identity(self, queries, resource, adapter, mode, evalue):
        if not self.enabled or resource.get("kind") != "blast_database":
            return None
        version = resource.get("version")
        if not isinstance(version, str) or not version.strip() or version == "SET_ON_SERVER":
            return None
        try:
            prefix = Path(resource["path"]).resolve()
            executable = shutil.which(adapter.executable)
            if not executable:
                return None
            # Flat volume/alias databases only. Untracked external DBLIST paths
            # must bypass caching rather than reuse potentially stale evidence.
            files = sorted(p for p in prefix.parent.iterdir() if p.is_file())
            if len(files) > 10000:
                return None
            relevant = [p for p in files if p.suffix.startswith((".n", ".p")) or p.name.startswith("tax")]
            if not any(p.name.startswith(prefix.name + ".") for p in relevant):
                return None
            identities = []
            for p in relevant:
                st = p.stat()
                record = [p.name, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino]
                if p.suffix in {".nal", ".pal"}:
                    alias = p.read_text()
                    for line in alias.splitlines():
                        tokens = shlex.split(line, comments=True)
                        if tokens and tokens[0] == "DBLIST":
                            for name in tokens[1:]:
                                child = (p.parent / name).resolve()
                                if child.parent != prefix.parent or not any(f.name.startswith(child.name + ".") for f in relevant):
                                    return None
                    record.append(hashlib.sha256(alias.encode()).hexdigest())
                identities.append(record)
            # Local configuration can change search semantics. Track common
            # configuration files and NCBI/BLAST environment as part of the key.
            config = []
            for folder in {Path.cwd(), Path.home(), Path(executable).parent, Path(os.environ.get("NCBI", "/etc"))}:
                for name in (".ncbirc", "ncbi.ini"):
                    p = folder / name
                    if p.is_file():
                        config.append([str(p), hashlib.sha256(p.read_bytes()).hexdigest()])
            return {"schema": 1, "queries": list(queries.items()), "version": version,
                    "database": str(prefix), "files": identities, "mode": mode, "evalue": evalue,
                    "executable": str(Path(executable).resolve()),
                    "binary_sha256": hashlib.sha256(Path(executable).read_bytes()).hexdigest(),
                    "implementation": [hashlib.sha256((Path(__file__).parent / n).read_bytes()).hexdigest()
                                       for n in ("specificity.py", "blast_cache.py")],
                    "runtime": adapter.status().get("runtime_limits"), "config": sorted(config),
                    "environment": {k: v for k, v in os.environ.items() if k.startswith(("BLAST", "NCBI"))}}
        except (OSError, ValueError, KeyError):
            return None

    def _load(self, path, key):
        try:
            if path.stat().st_size > self.max_entry_bytes:
                return None
            doc = json.loads(path.read_bytes())
            payload = doc["payload"]
            age = time.time() - doc["created"]
            if (doc["key"] != key or not 0 <= age < self.ttl or digest(payload) != doc["sha256"]
                    or payload["method"] != "blastn" or payload["evidence"].get("status") != "COMPLETED"
                    or payload["evidence"].get("returncode") != 0 or not isinstance(payload["hits"], list)):
                return None
            return payload
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def run(self, identity_factory, compute):
        identity = identity_factory()
        if identity is None:
            return compute()
        key = digest(identity)
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Fixed lock stripes bound lock-file count; same batch executes once.
            lock = locked(self.root / (key[:2] + ".lock"))
            lock.__enter__()
        except OSError:
            return compute()
        try:
            path = self.root / (key + ".json")
            cached = self._load(path, key)
            if cached is not None and identity_factory() == identity:
                cached["evidence"]["cache"] = {"status": "HIT", "key": key, "original_execution_reused": True}
                return cached
            payload = compute()  # Exceptions/fallbacks never become successful entries.
            evidence = payload["evidence"]
            if (payload["method"] != "blastn" or evidence.get("status") != "COMPLETED"
                    or evidence.get("returncode") != 0 or identity_factory() != identity):
                return payload
            data = encoded({"key": key, "created": time.time(), "sha256": digest(payload), "payload": payload})
            if len(data) <= self.max_entry_bytes:
                try:
                    with locked(self.root / "quota.lock"):
                        entries = sorted(self.root.glob("*.json"), key=lambda p: p.stat().st_mtime)
                        total = sum(p.stat().st_size for p in entries)
                        for old in entries:
                            if total + len(data) <= self.max_bytes:
                                break
                            size = old.stat().st_size
                            old.unlink()
                            total -= size
                        fd, temporary = tempfile.mkstemp(prefix=".entry-", dir=self.root)
                        try:
                            with os.fdopen(fd, "wb") as stream:
                                stream.write(data)
                                stream.flush()
                                os.fsync(stream.fileno())
                            os.replace(temporary, path)
                        finally:
                            Path(temporary).unlink(missing_ok=True)
                    evidence["cache"] = {"status": "STORED", "key": key}
                except OSError:
                    evidence["cache"] = {"status": "WRITE_FAILED"}
            else:
                evidence["cache"] = {"status": "ENTRY_TOO_LARGE"}
            return payload
        finally:
            lock.__exit__(None, None, None)
