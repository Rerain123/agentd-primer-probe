"""Gate expensive workflow acceptance on a completed full-database diagnostic."""
import json
import time
from pathlib import Path

path = Path('/evidence/internal-controls-20261012-r01/nt-profile-r04/result.json')
deadline = time.monotonic() + 3900
print('Waiting for bounded full-nt profile; no BLAST search started by acceptance yet', flush=True)
while time.monotonic() < deadline:
    if path.exists():
        try:
            result = json.loads(path.read_text())
        except json.JSONDecodeError:
            time.sleep(2)
            continue
        if result['status'] != 'COMPLETED' or result.get('returncode') != 0:
            raise SystemExit('Acceptance blocked by nt profile: '+str(result['status']))
        print('Full-nt profile completed; starting workflow acceptance', flush=True)
        break
    time.sleep(30)
else:
    raise SystemExit('Acceptance blocked: nt profile result unavailable after 3900 seconds')
