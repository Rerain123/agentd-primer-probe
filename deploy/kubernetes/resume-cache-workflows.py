"""Resume the existing acceptance batch; emit engineering summaries only."""
import contextlib
import hashlib
import importlib.util
import json
from pathlib import Path
import time
import traceback

from agentd.service import AgentDService

base = Path('/evidence/acceptance-20261012-r03')
root = base / 'continuation-20261013-r02'
root.mkdir(exist_ok=False)
spec = importlib.util.spec_from_file_location('driver', '/scripts/workflow-acceptance-driver.py')
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


def save(name, data):
    (root / (name + '.json')).write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def emit(data):
    print(json.dumps(data), flush=True)


start = time.monotonic()
try:
    original = json.loads((base / 'wf02-two-target-internal.json').read_text())
    batch_id = original['result']['batch_id']
    service = AgentDService(state_dir=base / 'jobs')
    before = service.get_wf02_v01_batch(batch_id)
    save('before', before)
    assert service.wf02_v01._require_gateway(before).SNAPSHOT_VERSION == 'wf02-wf01-execution-snapshot-v4'
    assert service.config['tools']['blast_runtime']['timeout_seconds'] is None
    steps = []

    def step(result):
        save('step-' + str(len(steps) + 1), result)
        summary = {k: result.get(k) for k in ['status','stage','next_action','terminal_report_ready']}
        summary['elapsed_seconds'] = round(time.monotonic()-start, 2)
        steps.append(summary)
        emit(summary)

    result = driver.advance_to_boundary(service, batch_id, step)
    after = service.get_wf02_v01_batch(batch_id)
    save('after', after)
    exports = []
    terminal = result.get('status') in {'COMPLETED','PARTIAL_PANEL_AVAILABLE',
                                       'FAILED_INCOMPLETE_TARGET_COVERAGE','FAILED_NO_COMPATIBLE_PANEL'}
    if terminal:
        report = service.get_wf02_v01_report(batch_id)
        save('report', report)
        assert result.get('terminal_report_ready'), 'Terminal report missing'
        for fmt in ['html','csv','pdf']:
            path = service.get_wf02_v01_export_path(batch_id, fmt)
            raw = path.read_bytes()
            assert raw, 'Empty export'
            if fmt == 'pdf':
                assert raw.startswith(b'%PDF-'), 'Invalid PDF signature'
            elif fmt == 'html':
                assert '<html' in raw.decode('utf-8').lower(), 'Invalid HTML'
            else:
                import csv
                import io
                assert list(csv.reader(io.StringIO(raw.decode('utf-8-sig')))), 'Invalid CSV'
            exports.append({'format':fmt,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
        restarted = AgentDService(state_dir=base / 'jobs')
        loaded = restarted.get_wf02_v01_batch(batch_id)
        assert loaded['status'] == after['status']
        assert restarted.wf02_v01._require_gateway(loaded).SNAPSHOT_VERSION == 'wf02-wf01-execution-snapshot-v4'
        # Lazy CSV/PDF creation updates export metadata and the resume token.
        # Compare after materialization, not against the pre-export token.
        stable_token = restarted.wf02_v01._resume_token(loaded)
        again = restarted.run_wf02_v01_until_blocked(batch_id)
        assert again['resume_token'] == stable_token, 'Terminal resume changed state'
        for entry in exports:
            raw = restarted.get_wf02_v01_export_path(batch_id, entry['format']).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == entry['sha256'], 'Terminal export changed on reload'
    after = service.get_wf02_v01_batch(batch_id)
    save('after', after)
    if terminal:
        save('report', service.get_wf02_v01_report(batch_id))
    relaxation = after.get('multiplex',{}).get('relaxation',{})
    summary = {'execution_status':'TERMINAL_VERIFIED' if terminal else 'BLOCKED',
               'workflow_status':result.get('status'), 'stage':result.get('stage'),
               'terminal_report_ready':result.get('terminal_report_ready'),
               'elapsed_seconds':round(time.monotonic()-start,2),
               'relaxation_status':relaxation.get('status'), 'exports':exports,
               'reload_idempotence_verified':terminal, 'steps':steps}
    save('summary', summary)
    emit(summary)
    raise SystemExit(0 if terminal else 2)
except Exception as exc:
    (root/'error.txt').write_text(traceback.format_exc())
    summary={'execution_status':'ERROR','exception_type':type(exc).__name__,
             'elapsed_seconds':round(time.monotonic()-start,2)}
    save('summary',summary)
    emit(summary)
    raise SystemExit(1)
