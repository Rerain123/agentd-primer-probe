"""Cache invalidation, failure recovery, bounded storage and frozen routing."""
import importlib
import json
import os
from pathlib import Path
import subprocess
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest


PACKAGES = ['agentd', 'agentd_workflow_snapshots.wf02_wf01_v4.agentd']


@pytest.fixture(params=PACKAGES)
def setup(request, tmp_path):
    module = importlib.import_module(request.param + '.specificity')
    executable = tmp_path / 'blastn'
    executable.write_bytes(b'fixture-program-v1')
    db = tmp_path / 'database'
    db.mkdir()
    (db/'nt.000.nsq').write_bytes(b'fixture-database-v1')
    (db/'nt.nal').write_text('DBLIST nt.000\n')
    resource = {'kind':'blast_database', 'path':str(db/'nt'), 'version':'fixture-v1'}
    provider = module.P2BatchSearchProvider({'tools':{'blast_cache':{
        'enabled':True, 'directory':str(tmp_path/'cache')}}})
    calls = []
    def search(queries, database, **kwargs):
        calls.append(1)
        provider.blast.last_search_evidence = {'status':'COMPLETED','returncode':0,'stdout':''}
        return [{'query_id':'q','fixture_value':1}], 'blastn'
    with patch.object(module.shutil, 'which', return_value=str(executable)), patch.object(provider.blast, 'search', side_effect=search):
        yield provider, resource, calls, module, tmp_path


def perform(setup, **kwargs):
    provider, resource, _, _, _ = setup
    return provider.search({'q':'ACGT'*5}, resource, mode='RELATED_BACKGROUND_NT', evalue=kwargs.get('evalue',1e-5))


def test_hit_skips_blast_and_recomputes_thermodynamics(setup):
    provider, _, calls, _, _ = setup
    with patch.object(provider, '_add_thermodynamics') as thermodynamics:
        first = perform(setup)
        second = perform(setup)
    assert calls == [1]
    assert first['hits'] == second['hits']
    assert second['raw_tool_output']['cache']['status'] == 'HIT'
    assert thermodynamics.call_count == 2


@pytest.mark.parametrize('change', ['version','database','executable','parameter','missing_database'])
def test_invalidation(setup, change):
    provider, resource, calls, _, root = setup
    perform(setup)
    if change == 'version': resource['version'] = 'fixture-v2'
    if change == 'database': (root/'database/nt.000.nsq').write_bytes(b'changed')
    if change == 'executable': (root/'blastn').write_bytes(b'new-program')
    if change == 'missing_database': (root/'database/nt.000.nsq').unlink()
    perform(setup, evalue=0.1 if change == 'parameter' else 1e-5)
    assert len(calls) == 2


@pytest.mark.parametrize('change', ['corrupt','checksum','expired'])
def test_bad_entry_is_recomputed(setup, change):
    _, _, calls, _, root = setup
    perform(setup)
    p=next((root/'cache').glob('*.json'))
    if change == 'corrupt':p.write_text('{broken')
    else:
        d=json.loads(p.read_text())
        if change == 'checksum':d['payload']['hits']=[{'invalid':True}]
        else:d['created']=0
        p.write_text(json.dumps(d))
    perform(setup)
    assert len(calls)==2


def test_failure_does_not_create_success(setup):
    provider, _, calls, module, root=setup
    with patch.object(provider.blast,'search',side_effect=module.ToolUnavailableError('fixture failure')):
        with pytest.raises(module.ToolUnavailableError):perform(setup)
    assert not list((root/'cache').glob('*.json'))
    perform(setup)
    assert len(calls)==1


def test_external_alias_bypasses_cache(setup):
    _, _, calls, _, root=setup
    (root/'database/nt.nal').write_text('DBLIST ../external\n')
    perform(setup);perform(setup)
    assert len(calls)==2


def test_concurrent_requests_execute_once(setup):
    _, _, calls, _, _=setup
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:perform(setup),range(2)))
    assert len(calls)==1
    assert results[0]['hits']==results[1]['hits']


def test_capacity_bounded(setup):
    provider, _, _, _, root=setup
    provider.cache.max_entry_bytes=1000
    provider.cache.max_bytes=1000
    perform(setup);perform(setup,evalue=0.1);perform(setup,evalue=0.2)
    assert sum(p.stat().st_size for p in (root/'cache').glob('*.json'))<=1000


def test_explicit_null_timeout(setup):
    _, _, _, module, root=setup
    adapter=module.BlastAdapter(runtime_config={'timeout_seconds':None,'mode_timeouts':{'RELATED_BACKGROUND_NT':None}})
    with patch.object(module.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')) as run:
        adapter._blast_search({'q':'ACGT'*5},root/'database/nt',mode='RELATED_BACKGROUND_NT',evalue=1e-5)
    assert run.call_args.kwargs['timeout'] is None


def test_cache_disabled_by_default(setup):
    _, resource, _, module, _=setup
    provider=module.P2BatchSearchProvider({})
    assert provider.cache.identity({'q':'ACGT'},resource,provider.blast,'TARGET',0.1) is None


def test_v4_snapshot_integrity():
    import hashlib
    from agentd_workflow_snapshots import wf02_wf01_v4 as metadata
    root=Path(__file__).resolve().parents[1]/'src/agentd_workflow_snapshots'
    revised=root/'wf02_wf01_v4/agentd';base=root/'wf02_wf01_v3/agentd'
    files=sorted((p for p in revised.rglob('*') if p.is_file() and '__pycache__' not in p.parts),key=lambda p:p.relative_to(revised).as_posix())
    digest=hashlib.sha256();changed=[]
    for p in files:
        rel=p.relative_to(revised)
        digest.update(rel.as_posix().encode());digest.update(b'\0');digest.update(p.read_bytes());digest.update(b'\0')
        if not (base/rel).exists() or p.read_bytes()!=(base/rel).read_bytes():changed.append(rel.as_posix())
    assert len(files)==metadata.SNAPSHOT_FILE_COUNT
    assert digest.hexdigest()==metadata.SNAPSHOT_TREE_SHA256
    assert changed==['blast_cache.py','specificity.py']


def test_database_mutation_during_execution_not_cached(setup):
    provider, _, _, _, root = setup
    def mutate(*args, **kwargs):
        (root/'database/nt.000.nsq').write_bytes(b'changed-during-search')
        provider.blast.last_search_evidence={'status':'COMPLETED','returncode':0}
        return [], 'blastn'
    with patch.object(provider.blast,'search',side_effect=mutate):
        perform(setup)
    assert not list((root/'cache').glob('*.json'))


def test_unavailable_cache_falls_back_to_search(setup):
    provider, _, calls, _, root = setup
    provider.cache.root = root/'file-not-directory'
    provider.cache.root.write_text('fixture')
    perform(setup)
    assert len(calls)==1


def test_service_unlimited_v4_keeps_v3_finite(tmp_path):
    from agentd.service import AgentDService
    service=AgentDService(state_dir=tmp_path, config_overrides={'tools':{'blast_runtime':{'timeout_seconds':None,'mode_timeouts':{'RELATED_BACKGROUND_NT':None}}}})
    assert service.wf02_wf01_gateway_v4.config['tools']['blast_runtime']['timeout_seconds'] is None
    assert service.wf02_wf01_gateway_v3.config['tools']['blast_runtime']['timeout_seconds']==600
    assert service.wf02_v01.gateway is service.wf02_wf01_gateway_v4
