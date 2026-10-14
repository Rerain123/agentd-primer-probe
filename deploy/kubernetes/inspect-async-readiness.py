"""Read-only deployment readiness; print operational metadata only."""
import json
import os
from collections import Counter
from pathlib import Path

result = {}
for name in ("wf01-v02-batches", "wf02-v01-batches", "wf01-background"):
    counts = Counter()
    paths = list((Path("/data/jobs") / name).glob("*.json"))
    if name == "wf01-background":
        paths = list((Path("/data/jobs") / name).glob("*/state.json"))
    for path in paths:
        value = json.loads(path.read_text())
        counts[str((value.get("background_execution") or {}).get("state") or value.get("state") or value.get("status"))] += 1
    result[name] = dict(counts)
config = json.loads(Path(os.environ.get("AGENTD_CONFIG", "/config/production.json")).read_text())
result["blast_runtime"] = config.get("tools", {}).get("blast_runtime", {})
result["blast_cache"] = config.get("tools", {}).get("blast_cache", {})
result["state_dir"] = os.environ.get("AGENTD_STATE_DIR")
print(json.dumps(result, ensure_ascii=True))
