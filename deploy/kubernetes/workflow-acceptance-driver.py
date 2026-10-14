"""Drain persisted WF02 units without treating an intermediate return as success."""


def advance_to_boundary(service, batch_id, on_step=lambda result: None):
    previous = None
    while True:
        result = service.run_wf02_v01_until_blocked(batch_id)
        on_step(result)
        if service.wf02_v01.is_background_boundary(result):
            return result
        token = result.get('resume_token')
        if not token or token == previous:
            raise RuntimeError('Acceptance driver detected no persisted progress')
        previous = token
