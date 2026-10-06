"""Bounded, process-local progress snapshots for in-flight file uploads.

Only opaque upload IDs, project scope, stage, and real byte counts are stored.
No filename, document text, or model cache path is exposed or persisted.
"""
from __future__ import annotations

import re
import threading
import time

_LOCK = threading.Lock()
_ITEMS: dict[str, dict] = {}
_TTL_SECONDS = 30 * 60
_MAX_ITEMS = 128
_UPLOAD_ID = re.compile(r"^[A-Za-z0-9_-]{16,80}$")


def _prune(now: float) -> None:
    expired = [key for key, item in _ITEMS.items() if now - item["updated"] > _TTL_SECONDS]
    for key in expired:
        _ITEMS.pop(key, None)
    if len(_ITEMS) >= _MAX_ITEMS:
        oldest = sorted(_ITEMS, key=lambda key: _ITEMS[key]["updated"])
        for key in oldest[:len(_ITEMS) - _MAX_ITEMS + 1]:
            _ITEMS.pop(key, None)


def begin(upload_id: str, project_id: str) -> bool:
    if not _UPLOAD_ID.fullmatch(upload_id or "") or not project_id:
        return False
    now = time.monotonic()
    with _LOCK:
        _prune(now)
        _ITEMS[upload_id] = {"project_id": project_id, "status": "running",
                             "stage": "receiving_file", "current": None,
                             "total": None, "updated": now}
    return True


def update(upload_id: str, project_id: str, stage: str, *, current=None, total=None) -> None:
    if not _UPLOAD_ID.fullmatch(upload_id or ""):
        return
    now = time.monotonic()
    with _LOCK:
        _prune(now)
        item = _ITEMS.get(upload_id)
        if item is None or item["project_id"] != project_id or item["status"] != "running":
            return
        item.update(stage=stage, current=current, total=total, updated=now)


def finish(upload_id: str, project_id: str, status: str) -> None:
    if status not in {"completed", "failed"}:
        return
    now = time.monotonic()
    with _LOCK:
        item = _ITEMS.get(upload_id)
        if item is None or item["project_id"] != project_id:
            return
        item.update(status=status, stage=status, current=None, total=None, updated=now)


def get(upload_id: str, project_id: str) -> dict | None:
    now = time.monotonic()
    with _LOCK:
        _prune(now)
        item = _ITEMS.get(upload_id)
        if item is None or item["project_id"] != project_id:
            return None
        return {key: value for key, value in item.items() if key != "updated" and key != "project_id"}
