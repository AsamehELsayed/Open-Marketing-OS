"""DEV-015 regression: retrieval initialization must not block async upload."""
from contextlib import contextmanager
import asyncio
import io
import threading

from starlette.datastructures import Headers, UploadFile

from app import deps
from app.routes import files as files_route
from app.services.files.ingest import FileRecord, IngestResult


def test_project_upload_runs_retrieval_and_ingest_off_event_loop(monkeypatch):
    event_loop_thread = threading.get_ident()
    worker_threads = {}
    worker_started = threading.Event()
    worker_finished = threading.Event()
    release_worker = threading.Event()

    @contextmanager
    def db_context():
        worker_threads["db"] = threading.get_ident()
        yield object()

    def retrieval_runtime(_root, _project_id):
        worker_threads["retrieval"] = threading.get_ident()
        worker_started.set()
        # Keep the worker occupied while the async test checks loop progress.
        try:
            release_worker.wait(timeout=10)
        finally:
            worker_finished.set()
        return object(), object()

    def ingest(_conn, **kwargs):
        worker_threads["ingest"] = threading.get_ident()
        assert kwargs["data"] == b"synthetic upload"
        return IngestResult(
            record=FileRecord(
                file_id="file-1", project_id="projA", original_name="brief.txt",
                safe_name="internal.txt", mime_detected="text/plain",
                size=len(kwargs["data"]), sha256="a" * 64, kind="document",
                extraction="ready",
            ),
            attach_scope="project", indexed=True, quarantine=[], note="",
        )

    monkeypatch.setattr(deps, "get_db", db_context)
    monkeypatch.setattr(files_route, "ingest", ingest)
    monkeypatch.setattr(files_route.files_repo, "get_file", lambda *_args: None)
    from app.services import knowledge_index
    monkeypatch.setattr(knowledge_index, "retrieval_runtime", retrieval_runtime)
    monkeypatch.setattr(
        knowledge_index, "retrieval_status_for_store",
        lambda _store, **_kwargs: {"search_mode": "HYBRID", "vector_status": "AVAILABLE"},
    )

    upload = UploadFile(
        file=io.BytesIO(b"synthetic upload"), filename="brief.txt",
        headers=Headers({"content-type": "text/plain"}),
    )

    async def exercise_upload():
        task = asyncio.create_task(files_route.upload_file(
            file=upload, project_id="projA", attach_scope="project",
            conversation_id="",
        ))
        try:
            # Wait for the blocking operation to enter the worker without
            # assuming how quickly this host schedules its thread pool.
            entered = await asyncio.wait_for(
                asyncio.to_thread(worker_started.wait, 5), timeout=6,
            )
            assert entered
            # The initializer is still blocked. This loop turn must run before
            # it can finish, proving the async server loop stayed responsive.
            await asyncio.sleep(0)
            assert not release_worker.is_set()
            assert not worker_finished.is_set()
            assert not task.done()
        finally:
            release_worker.set()
        return await task

    response = asyncio.run(exercise_upload())

    assert response.status_code == 201
    assert response.body
    assert worker_threads["db"] == worker_threads["retrieval"]
    assert worker_threads["db"] == worker_threads["ingest"]
    assert worker_threads["db"] != event_loop_thread
