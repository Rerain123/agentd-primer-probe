"""Serialize benchmark Jobs so they do not compete for storage bandwidth."""
import os
import time
from pathlib import Path
path=Path('/evidence')/os.environ.get('PREVIOUS_ROOT','nt-subset-20261012-r04')/os.environ['PREVIOUS_CASE']/'summary.json'
while not path.exists():
    time.sleep(10)
print('Previous benchmark finished; proceeding with independent case',flush=True)
