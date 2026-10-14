from pathlib import Path
import hashlib
import json
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from agentd.config import load_config
from agentd.database import DatabaseCatalog
from agentd.errors import InputValidationError
from agentd.internal_control import InternalControlDesigner
from agentd.service import AgentDService
from agentd.wf01_v02 import WF01V02Preflight
from agentd_workflow_snapshots.wf02_wf01_v2.agentd.wf01_v02 import WF01V02Preflight as V2Preflight


@pytest.mark.parametrize('engine', [WF01V02Preflight, V2Preflight])
@pytest.mark.parametrize('name,canonical', [('羊','sheep'),('Ovis aries','sheep'),('山羊','goat'),('Capra hircus','goat'),({'taxid':'9940'},'sheep'),({'taxid':'9925'},'goat'),({'taxid':'9823'},'pig')])
def test_host_name_and_taxid_normalize_without_false_missing_database(engine, name, canonical):
    preflight = engine({})
    result = preflight._normalise_object(name, role='HOST', target_id='T1', ordinal=1)
    assert result['normalized_name'] == canonical
    assert preflight.issues == []
    # M1 is only normalization, never a completed specificity claim.
    assert result['resolution_status'] == 'NORMALIZED'


def test_new_parent_binding_and_old_parent_resume_are_separate(tmp_path):
    service = AgentDService(state_dir=tmp_path)
    v1, v2 = service.wf02_wf01_gateway_v1, service.wf02_wf01_gateway_v2
    v3 = service.wf02_wf01_gateway_v3
    v4 = service.wf02_wf01_gateway_v4
    assert service.wf02_v01.gateway is v4
    assert service.wf02_v01.dependency_contracts == v4.dependency_contracts()
    assert service.wf02_v01._require_gateway({"dependency_contracts":v4.dependency_contracts()}) is v4
    old = {'dependency_contracts': v1.dependency_contracts()}
    new = {'dependency_contracts': v2.dependency_contracts()}
    assert service.wf02_v01._require_gateway(old) is v1
    assert service.wf02_v01._require_gateway(new) is v2
    assert service.wf02_v01._require_gateway({'dependency_contracts':v3.dependency_contracts()}) is v3
    assert service.wf02_v01._require_gateway({}) is v1
    assert v2.wf01_service.store.root != v1.wf01_service.store.root
    assert type(v2.wf01_service).__module__.startswith('agentd_workflow_snapshots.wf02_wf01_v2.')
    with pytest.raises(InputValidationError):
        v3.for_batch({'dependency_contracts': {'wf01_execution': {'snapshot_version':'unknown'}}})


def test_v2_snapshot_has_only_registered_changes():
    from agentd_workflow_snapshots import wf02_wf01_v2 as metadata
    root = Path(__file__).resolve().parents[1] / 'src/agentd_workflow_snapshots'
    base, revised = root/'wf02_wf01_v1/agentd', root/'wf02_wf01_v2/agentd'
    digest = hashlib.sha256()
    changes = []
    files = sorted((p for p in revised.rglob('*') if p.is_file() and '__pycache__' not in p.parts), key=lambda p:p.relative_to(revised).as_posix())
    assert len(files) == metadata.SNAPSHOT_FILE_COUNT
    for path in files:
        rel = path.relative_to(revised)
        digest.update(rel.as_posix().encode()); digest.update(b'\0'); digest.update(path.read_bytes()); digest.update(b'\0')
        if path.read_bytes() != (base/rel).read_bytes():
            changes.append(rel.as_posix())
    assert digest.hexdigest() == metadata.SNAPSHOT_TREE_SHA256
    assert changes == ['internal_control.py', 'wf01_v02.py']


def test_p5_offline_status_uses_catalog_instead_of_requiring_entrez(tmp_path):
    resources = {}
    config = load_config(overrides={'internal_control': {'entrez': {'enabled':False}}})
    for species in config['internal_control']['supported_species']:
        fasta = tmp_path/f'{species}.fasta'
        fasta.write_text(''.join(f'>fixture-{i}\n'+('ACGT'*30)+'\n' for i in range(5)))
        resources[f'internal:{species}:ACTB:sequences'] = {'kind':'fasta','path':str(fasta),'version':'unit-test-only'}
    catalog_file=tmp_path/'catalog.json'; catalog_file.write_text(json.dumps({'resources':resources}))
    catalog=DatabaseCatalog(config,catalog_path=catalog_file)
    with patch('agentd.internal_control.Primer3Adapter.status', return_value={'available':True}), patch('agentd.internal_control.MafftAdapter.status', return_value={'available':True}), patch('agentd.internal_control.P2SpecificityBatchService.status', return_value={'available':True}):
        result=InternalControlDesigner(config,catalog).status()
    assert result['available'] is True
    assert result['dependencies']['NCBI_ENTREZ']['enabled'] is False
    assert result['dependencies']['OFFLINE_SEQUENCES']['available'] is True


def test_chicken_18s_does_not_use_rat_accession():
    assert load_config()['internal_control']['refseq']['chicken']['18S rRNA'] == 'AF173612.1'
