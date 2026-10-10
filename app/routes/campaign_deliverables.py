"""Project and campaign-scoped API for Markdown campaign deliverables."""
import re
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, StrictInt

from app import deps
from app.services import campaign_deliverables as service


router = APIRouter(prefix="/api", tags=["campaign-deliverables"])


class CreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    title: str
    content_md: str
    platform: str | None = None


class RevisePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: StrictInt
    type: str | None = None
    title: str | None = None
    content_md: str | None = None
    platform: str | None = None


class TransitionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: StrictInt
    status: str


def _ok(data):
    return {"ok": True, "data": data}


def _translate_error(exc):
    if isinstance(exc, service.DeliverableNotFound):
        raise HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, service.DeliverableConflict):
        raise HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, service.DeliverableValidationError):
        raise HTTPException(status_code=422, detail=str(exc))
    raise exc


def _attachment_filename(value: str, fallback: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-_")[:100]
    return safe or fallback


@router.get("/projects/{project_id}/campaigns/{campaign_id}/deliverables")
def list_deliverables(project_id: str, campaign_id: str):
    with deps.get_db() as conn:
        try:
            return _ok(service.list_deliverables(conn, project_id, campaign_id))
        except (service.DeliverableNotFound, service.DeliverableValidationError) as exc:
            _translate_error(exc)


@router.post("/projects/{project_id}/campaigns/{campaign_id}/deliverables")
def create_deliverable(project_id: str, campaign_id: str, payload: CreatePayload):
    with deps.get_db() as conn:
        try:
            return _ok(service.create_manual(
                conn, project_id, campaign_id, payload.model_dump()
            ))
        except (service.DeliverableNotFound, service.DeliverableValidationError) as exc:
            _translate_error(exc)


@router.get("/projects/{project_id}/campaigns/{campaign_id}/deliverables/{deliverable_id}")
def get_deliverable(project_id: str, campaign_id: str, deliverable_id: str):
    with deps.get_db() as conn:
        try:
            return _ok(service.get_deliverable(
                conn, project_id, campaign_id, deliverable_id
            ))
        except service.DeliverableNotFound as exc:
            _translate_error(exc)


@router.put("/projects/{project_id}/campaigns/{campaign_id}/deliverables/{deliverable_id}")
def revise_deliverable(project_id: str, campaign_id: str, deliverable_id: str,
                       payload: RevisePayload):
    data = payload.model_dump(exclude_unset=True)
    expected_version = data.pop("expected_version")
    with deps.get_db() as conn:
        try:
            return _ok(service.revise(
                conn, project_id, campaign_id, deliverable_id,
                expected_version, data,
            ))
        except (service.DeliverableNotFound, service.DeliverableConflict,
                service.DeliverableValidationError) as exc:
            _translate_error(exc)


@router.post("/projects/{project_id}/campaigns/{campaign_id}/deliverables/{deliverable_id}/status")
def transition_deliverable(project_id: str, campaign_id: str, deliverable_id: str,
                           payload: TransitionPayload):
    with deps.get_db() as conn:
        try:
            return _ok(service.transition(
                conn, project_id, campaign_id, deliverable_id,
                payload.expected_version, payload.status,
            ))
        except (service.DeliverableNotFound, service.DeliverableConflict,
                service.DeliverableValidationError) as exc:
            _translate_error(exc)


@router.get("/projects/{project_id}/campaigns/{campaign_id}/deliverables/{deliverable_id}/history")
def deliverable_history(project_id: str, campaign_id: str, deliverable_id: str):
    with deps.get_db() as conn:
        try:
            return _ok(service.history(
                conn, project_id, campaign_id, deliverable_id
            ))
        except service.DeliverableNotFound as exc:
            _translate_error(exc)


@router.get("/projects/{project_id}/campaigns/{campaign_id}/deliverables/{deliverable_id}/export.md")
def export_markdown(project_id: str, campaign_id: str, deliverable_id: str):
    with deps.get_db() as conn:
        try:
            row, markdown = service.markdown_export(
                conn, project_id, campaign_id, deliverable_id
            )
        except service.DeliverableNotFound as exc:
            _translate_error(exc)
    filename = _attachment_filename(row["title"], row["type"])
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename + '.md')}"},
    )


@router.get("/projects/{project_id}/campaigns/{campaign_id}/deliverables/exports/package.zip")
def export_package(project_id: str, campaign_id: str):
    with deps.get_db() as conn:
        try:
            package = service.package_export(conn, project_id, campaign_id)
        except service.DeliverableNotFound as exc:
            _translate_error(exc)
    return Response(
        content=package,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="campaign-deliverables.zip"'},
    )
