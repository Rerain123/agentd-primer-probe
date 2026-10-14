"""Runtime configuration must not change search policy or frozen parent behavior."""
import hashlib
import importlib
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

PACKAGES = ['agentd', 'agentd_workflow_snapshots.wf02_wf01_v3.agentd']


@pytest.mark.parametrize('package', PACKAGES)
def test_background_timeout_threads_and_failure_evidence(package, tmp_path):
    module = importlib.import_module(package + '.specificity')
    adapter = module.P2BatchSearchProvider({'tools': {'blast_runtime': {
        'num_threads': 4, 'mode_timeouts': {'RELATED_BACKGROUND_NT': 3600}}}}).blast
    with patch.object(module.subprocess, 'run', side_effect=subprocess.TimeoutExpired('blastn', 3600)) as run:
        with pytest.raises(module.ToolUnavailableError):
            adapter._blast_search({'q':'ACGT'*5}, tmp_path/'nt', evalue=1e-5, mode='RELATED_BACKGROUND_NT')
    assert run.call_args.kwargs['timeout'] == 3600
    command = run.call_args.args[0]
    assert command[command.index('-num_threads')+1] == '4'
    assert command[command.index('-evalue')+1] == '1e-05'
    assert command[command.index('-task')+1] == 'blastn-short'
    assert adapter.last_search_evidence['status'] == 'TIMED_OUT'
    assert adapter.last_search_evidence['runtime_limits'] == {'num_threads':4,'timeout_seconds':3600}
    assert 'returncode' in adapter.last_search_evidence


@pytest.mark.parametrize('package', PACKAGES)
def test_subject_mode_does_not_pass_incompatible_threads_or_background_timeout(package, tmp_path):
    module = importlib.import_module(package + '.specificity')
    adapter = module.BlastAdapter(runtime_config={'num_threads':4,'mode_timeouts':{'RELATED_BACKGROUND_NT':3600}})
    fasta = tmp_path/'reference.fasta'
    fasta.write_text('>q\nACGTACGTACGTACGTACGT\n')
    with patch.object(module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
        adapter._blast_search({'q':'ACGT'*5}, fasta, evalue=1000, mode='TARGET_HIT')
    assert run.call_args.kwargs['timeout'] == 600
    assert '-num_threads' not in run.call_args.args[0]
    assert '-subject' in run.call_args.args[0]
    assert adapter.last_search_evidence['runtime_limits']['num_threads'] == 1


@pytest.mark.parametrize('package', PACKAGES)
@pytest.mark.parametrize('settings', [[], {'num_threads':0}, {'num_threads':True},
    {'timeout_seconds':-1}, {'timeout_seconds':float('inf')}, {'timeout_seconds':'3600'},
    {'mode_timeouts':[]}, {'mode_timeouts':{'TARGET':0}}, {'mode_timeouts':{'':1}},
    {'num_thread':4}])
def test_invalid_runtime_fails_before_execution(package, settings):
    module = importlib.import_module(package + '.specificity')
    with pytest.raises(module.InputValidationError):
        module.BlastAdapter(runtime_config=settings)


def test_v1_v2_ignore_new_runtime_settings():
    for version in ['v1', 'v2']:
        module = importlib.import_module(f'agentd_workflow_snapshots.wf02_wf01_{version}.agentd.specificity')
        adapter = module.P2BatchSearchProvider({'tools':{'blast_runtime':{'num_threads':4,'timeout_seconds':3600}}}).blast
        with patch.object(module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
            adapter._blast_search({'q':'ACGT'*5}, Path('fixture-nt'), evalue=1e-5, mode='RELATED_BACKGROUND_NT')
        assert run.call_args.kwargs['timeout'] == 600
        assert '-num_threads' not in run.call_args.args[0]


def test_v3_has_only_registered_runtime_changes():
    from agentd_workflow_snapshots import wf02_wf01_v3 as metadata
    root = Path(__file__).resolve().parents[1] / 'src/agentd_workflow_snapshots'
    base, revised = root/'wf02_wf01_v2/agentd', root/'wf02_wf01_v3/agentd'
    files = sorted((p for p in revised.rglob('*') if p.is_file() and '__pycache__' not in p.parts), key=lambda p:p.relative_to(revised).as_posix())
    assert len(files) == metadata.SNAPSHOT_FILE_COUNT
    digest = hashlib.sha256()
    changed = []
    for path in files:
        relative = path.relative_to(revised)
        digest.update(relative.as_posix().encode()); digest.update(b'\0'); digest.update(path.read_bytes()); digest.update(b'\0')
        if path.read_bytes() != (base/relative).read_bytes():
            changed.append(relative.as_posix())
    assert digest.hexdigest() == metadata.SNAPSHOT_TREE_SHA256
    assert changed == ['internal_control.py','service.py','specificity.py','workflows/wf01.py']
