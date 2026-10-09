"""DEV-031 API: project-isolated client profiles and editable briefs."""
import json
import re
import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app import deps
from app.database import repos
from app.services import business_workspace as service

router = APIRouter(prefix="/api", tags=["business-workspace"])


class Payload(BaseModel):
    model_config = {"extra": "allow"}


class BriefOutputValidationError(ValueError):
    def __init__(self, message: str, *, provider: str = "", model: str = ""):
        super().__init__(message)
        self.provider = provider
        self.model = model


def has_unsupported_citations(markdown, allowed_ids):
    """Accept only exact, bracketed references copied from scoped evidence."""
    canonical = []
    for match in re.finditer(r"\[([^\]\r\n]*)\]", markdown):
        source_id = match.group(1)
        if ":" not in source_id:
            continue
        if source_id not in allowed_ids:
            return True
        canonical.append((match.start(1), match.end(1), source_id))

    urls = [match.span() for match in re.finditer(r"https?://[^\s)\]]+", markdown, re.IGNORECASE)]
    source_like_tokens = re.finditer(
        r"(?<![\w/])([A-Za-z0-9_./-]+:[A-Za-z0-9_./-]+)(?![\w/])", markdown
    )
    for match in source_like_tokens:
        token = match.group(1)
        if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", token):
            continue
        if any(start <= match.start(1) < end for start, end in urls):
            continue
        if any(start == match.start(1) and end == match.end(1) and source_id == token
               for start, end, source_id in canonical):
            continue
        return True
    return False


def ok(data):
    return {"ok": True, "data": data}


def failure(exc):
    if isinstance(exc, RuntimeError):
        raise HTTPException(409, str(exc))
    raise HTTPException(422, str(exc))


@router.post("/projects")
def create_client(payload: Payload):
    data = payload.model_dump()
    name = data.get("business_name", data.get("name", ""))
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(422, "business_name is required")
    profile_input = dict(data)
    profile_input["business_name"] = name.strip()
    try:
        supplied_profile = service.validate_profile_fields(profile_input)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    profile_values = {field: supplied_profile.get(field, "") for field in service.FIELDS}
    pid = uuid.uuid4().hex
    stamp = service.now()
    with deps.get_db() as conn:
        settings = {"isolated_client_workspace": True}
        conn.execute("INSERT INTO projects(id,name,website,goal,status,settings_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                     (pid, profile_values["business_name"], profile_values["website"], "", "active", json.dumps(settings), stamp, stamp))
        columns = ("project_id", *service.FIELDS, "created_at", "updated_at")
        column_sql = ",".join(columns)
        placeholders = ",".join("?" for _ in columns)
        values = (pid, *(profile_values[field] for field in service.FIELDS), stamp, stamp)
        conn.execute(f"INSERT INTO business_profiles({column_sql}) VALUES({placeholders})", values)
        conn.commit()
        return ok(service.get_profile(conn, pid))


@router.get("/projects/{project_id}/business-profile")
def read_profile(project_id: str):
    with deps.get_db() as conn:
        row = service.get_profile(conn, project_id)
        if row is None: raise HTTPException(404, "unknown project")
        return ok(row)


@router.put("/projects/{project_id}/business-profile")
def write_profile(project_id: str, payload: Payload):
    data = payload.model_dump()
    if data.get("project_id", project_id) != project_id: raise HTTPException(422, "project_id must match path")
    with deps.get_db() as conn:
        try: row = service.update_profile(conn, project_id, data)
        except (ValueError, RuntimeError) as exc: failure(exc)
        if row is None: raise HTTPException(404, "unknown project")
        return ok(row)


@router.get("/projects/{project_id}/business-brief")
def read_brief(project_id: str):
    with deps.get_db() as conn:
        row = service.get_brief(conn, project_id)
        if row is None: raise HTTPException(404, "unknown project")
        return ok(row)


@router.put("/projects/{project_id}/business-brief")
def write_brief(project_id: str, payload: Payload):
    data = payload.model_dump()
    if data.get("project_id", project_id) != project_id: raise HTTPException(422, "project_id must match path")
    with deps.get_db() as conn:
        try: row = service.save_brief(conn, project_id, data)
        except (ValueError, RuntimeError) as exc: failure(exc)
        if row is None: raise HTTPException(404, "unknown project")
        return ok(row)


@router.post("/projects/{project_id}/business-brief/generate")
def generate_brief(project_id: str, payload: Payload):
    data = payload.model_dump()
    if data.get("project_id", project_id) != project_id: raise HTTPException(422, "project_id must match path")
    with deps.get_db() as conn:
        profile = service.get_profile(conn, project_id)
        if profile is None: raise HTTPException(404, "unknown project")
        if profile["brief_md"].strip() and not data.get("overwrite_confirmed"):
            raise HTTPException(409, "confirm overwrite to regenerate this saved brief")
        profile_rev, brief_rev = profile["profile_revision"], profile["brief_revision"]
        from app.services.rag.scoped_retrieval import retrieve_scoped
        retrieval = retrieve_scoped(conn, "business audience offer differentiators", project_id=project_id, mode="lexical", k_fts=8, k_sem=0)
        hits = retrieval["hits"]
        evidence = [{"source_id": f'{h.get("document_id")}:{h.get("chunk_id")}', "path": h.get("path", ""), "text": h.get("text", "")[:1200]} for h in hits]
        system = ("Create an editable business brief in Markdown using exactly these four headings: "
                  "## Persisted user facts, ## Retrieved evidence, ## AI suggestions, and "
                  "## Unknown or unsupported information. Keep user-provided facts, retrieved "
                  "evidence, suggestions, and missing information in their matching sections. "
                  "Cite retrieved facts only with the exact syntax [document_id:chunk_id], "
                  "using an ID copied from the supplied evidence. Do not use bare IDs, "
                  "parenthesized IDs, or any other citation format or colon-delimited "
                  "source reference. "
                  "Never invent a citation or present an assumption as a fact. State when "
                  "evidence is insufficient. Return Markdown only.")
        user = json.dumps({"profile": {k: profile.get(k, "") for k in service.FIELDS}, "evidence": evidence})
        try:
            from app.routes.graph_runtime import _get_model_router
            router = _get_model_router()
            response, call = router.complete(conn, turn_id=f"brief-{uuid.uuid4().hex[:12]}", project_id=project_id,
                    system=system, messages=[{"role": "user", "content": user}], tools=[], mode="AUTO", opts={"max_tokens": 1800})
            text = getattr(response, "text", "") or ""
            provider = str(getattr(call, "provider", "") or "")
            model = str(getattr(call, "model", "") or "")
            if not text.strip():
                raise BriefOutputValidationError(
                    "The model returned an empty brief. Try again or write the brief manually.",
                    provider=provider, model=model,
                )
            allowed = {e["source_id"] for e in evidence}
            if has_unsupported_citations(text, allowed):
                raise BriefOutputValidationError(
                    "The model returned an unsupported or noncanonical source reference, "
                    "so the brief was not saved. "
                    "Try again or write the brief manually.", provider=provider, model=model,
                )
            required_sections = (
                r"(?im)^\s*#{1,6}\s*Persisted user facts\b",
                r"(?im)^\s*#{1,6}\s*Retrieved evidence\b",
                r"(?im)^\s*#{1,6}\s*AI suggestions\b",
                r"(?im)^\s*#{1,6}\s*(?:Unknown or unsupported information|Missing information)\b",
            )
            if any(re.search(pattern, text) is None for pattern in required_sections):
                raise BriefOutputValidationError(
                    "The model response did not separate facts, evidence, suggestions, and missing information. "
                    "The brief was not saved; try again or write it manually.",
                    provider=provider, model=model,
                )
            meta = {"status": "generated", "provider": provider, "model": model, "error": ""}
            conn.execute("UPDATE business_profiles SET brief_md=?,sources_json=?,generation_json=?,brief_revision=brief_revision+1,updated_at=? WHERE project_id=? AND profile_revision=? AND brief_revision=?",
                         (text, json.dumps(evidence), json.dumps(meta), service.now(), project_id, profile_rev, brief_rev))
            if conn.execute("SELECT changes()").fetchone()[0] != 1:
                conn.rollback(); raise HTTPException(409, "profile or brief changed during generation; regenerate from the latest revision")
            conn.commit()
            return ok(service.get_brief(conn, project_id))
        except HTTPException: raise
        except Exception as exc:
            # Preserve the prior brief and provenance; expose a plain-language generation failure.
            provider = str(getattr(exc, "provider", "") or "")
            model = str(getattr(exc, "model", "") or "")
            failure_code = str(getattr(exc, "failure_code", "") or "")
            invocation_started = getattr(exc, "invocation_started", True)
            failure_text = str(exc).lower()
            if isinstance(exc, BriefOutputValidationError):
                failure_code = "invalid_model_output"
                message = str(exc)
            elif ("local ai is unavailable" in failure_text and
                    ("openai is not configured" in failure_text or
                     "openai is not connected" in failure_text)):
                provider = ""
                model = ""
                failure_code = "no_provider_ready"
                message = (
                    "No AI provider is ready. The local model is unavailable and "
                    "OpenAI is not connected. Connect a provider in Settings or "
                    "make a local model available, then try again."
                )
            elif "openai is not configured" in failure_text or "cloud ai is not configured" in failure_text:
                provider = provider or "openai"
                failure_code = "missing_credentials"
                message = "OpenAI is not connected. Connect it in Settings or choose an available local model."
            elif "openrouter is not configured" in failure_text or "openrouter is not connected" in failure_text:
                provider = provider or "openrouter"
                failure_code = "missing_credentials"
                message = "OpenRouter is not connected. Connect it in Settings or choose an available local model."
            elif failure_code == "authentication":
                message = (
                    "The provider rejected its credentials. Check the connection "
                    "in Settings or choose another available model."
                )
            elif failure_code == "request_configuration" or any(
                    phrase in (failure_text + " " + str(getattr(exc, "failure_reason", "")).lower())
                    for phrase in ("model not found", "unknown model", "model is unavailable")):
                message = (
                    "This model is unavailable for the request. Choose another "
                    "available model in Settings."
                )
            elif invocation_started is False and provider in {"openai", "openrouter"}:
                message = (
                    "No credentials are configured for this provider. Connect it "
                    "in Settings or choose an available local model."
                )
            elif invocation_started is False and provider == "local":
                message = (
                    "The selected local model is not running or available. Start "
                    "it in Settings or choose a connected provider."
                )
            elif failure_code == "timeout":
                message = "The model request timed out. Try again or choose another model."
            else:
                message = (
                    "The model could not generate this brief right now. Check the "
                    "provider in Settings or try again."
                )
            raise HTTPException(503, {
                "status": "failed", "provider": provider, "model": model,
                "code": failure_code or "provider_failure", "error": message,
            })
