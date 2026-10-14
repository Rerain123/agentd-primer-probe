import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

path = Path(__file__).resolve().parents[1] / 'deploy/kubernetes/workflow-acceptance-driver.py'
spec = importlib.util.spec_from_file_location('acceptance_driver', path)
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


def service_for(results):
    from agentd.wf02_v01 import WF02V01BatchService
    iterator = iter(results)
    return SimpleNamespace(run_wf02_v01_until_blocked=lambda _: next(iterator),
                           wf02_v01=WF02V01BatchService)


def test_stage_boundary_is_continued_until_terminal_failure():
    results = [
        {'status':'READY_FOR_RELAXATION','next_action':'RUN_RELAXATION','resume_token':'a'},
        {'status':'RELAXING_PANELS','next_action':'CONTINUE_RELAXATION','resume_token':'b'},
        {'status':'FAILED_NO_COMPATIBLE_PANEL','next_action':'GET_REPORT','resume_token':'c'},
    ]
    observed = []
    result = driver.advance_to_boundary(service_for(results), 'fixture', observed.append)
    assert result['status'] == 'FAILED_NO_COMPATIBLE_PANEL'
    assert observed == results


def test_user_or_resource_boundary_not_retried():
    result={'status':'WAITING_USER','next_action':'SUBMIT_RESPONSE','resume_token':'a'}
    assert driver.advance_to_boundary(service_for([result]),'fixture') == result


def test_stalled_execution_fails_instead_of_infinite_retry():
    result={'status':'READY_FOR_RELAXATION','next_action':'RUN_RELAXATION','resume_token':'a'}
    with pytest.raises(RuntimeError,match='no persisted progress'):
        driver.advance_to_boundary(service_for([result,result]),'fixture')
