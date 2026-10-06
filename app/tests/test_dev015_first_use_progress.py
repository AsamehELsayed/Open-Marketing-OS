"""DEV-015 truthful first-use model and indexing progress primitives."""
import io
import uuid

from app.services import upload_progress
from app.services.rag.embeddings import _ProgressTqdm


def test_upload_progress_is_project_scoped_and_has_terminal_success_and_failure():
    project_a_id = uuid.uuid4().hex
    project_b_id = uuid.uuid4().hex
    failed_id = uuid.uuid4().hex
    assert upload_progress.begin(project_a_id, "project-a")
    upload_progress.update(project_a_id, "project-a", "downloading_model", current=125, total=500)
    assert upload_progress.get(project_a_id, "project-a") == {
        "status": "running", "stage": "downloading_model", "current": 125, "total": 500,
    }
    assert upload_progress.get(project_a_id, "project-b") is None
    upload_progress.finish(project_a_id, "project-a", "completed")
    assert upload_progress.get(project_a_id, "project-a") == {
        "status": "completed", "stage": "completed", "current": None, "total": None,
    }

    assert upload_progress.begin(failed_id, "project-b")
    upload_progress.finish(failed_id, "project-b", "failed")
    assert upload_progress.get(failed_id, "project-b")["status"] == "failed"
    assert upload_progress.get(failed_id, "project-a") is None


def test_tqdm_callback_reports_real_download_bytes_without_synthesizing_total():
    events = []
    bar_type = _ProgressTqdm(events.append)
    bar = bar_type(total=100, file=io.StringIO(), mininterval=0)
    bar.update(37)
    bar.close()
    assert events[-1] == {"stage": "downloading_model", "current": 37, "total": 100}


def test_progress_observer_failure_does_not_fail_model_provider():
    from app.services.rag.embeddings import LocalMultilingualE5Embeddings
    provider = LocalMultilingualE5Embeddings(progress_callback=lambda _event: (_ for _ in ()).throw(RuntimeError()))
    provider._report_progress({"stage": "loading_local_model"})
