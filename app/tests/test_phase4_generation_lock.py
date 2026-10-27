import threading

from app.services import campaign_flow


def test_inflight_generation_is_rejected_and_lock_released(monkeypatch):
    started, finish = threading.Event(), threading.Event()
    monkeypatch.setattr(campaign_flow.deliverables, '_require_scope', lambda *args: {})
    def generate(*args, **kwargs):
        started.set()
        assert finish.wait(5)
        return {'ok': True}
    monkeypatch.setattr(campaign_flow, '_generate_from_plan', generate)
    thread = threading.Thread(target=campaign_flow.generate_from_plan,
                              args=(None,), kwargs={'project_id': 'p', 'campaign_id': 'c'})
    thread.start()
    assert started.wait(5)
    try:
        try:
            campaign_flow.generate_from_plan(None, project_id='p', campaign_id='c')
            assert False, 'overlapping request was accepted'
        except campaign_flow.FlowError as exc:
            assert exc.status == 409
            assert exc.code == 'generation_in_progress'
    finally:
        finish.set()
        thread.join(5)
    assert not thread.is_alive()
    res = campaign_flow.generate_from_plan(None, project_id='p', campaign_id='c')
    assert res.get('ok') is True
    assert 'elapsed_s' in res
