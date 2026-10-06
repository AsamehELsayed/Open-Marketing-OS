"""Graph execution events — wire-compatible with the current SSE transport.

The persisted execution_events row shape and the SSE envelope emitted by
app/routes/chat.py + app/services/turns.py are FROZEN. v1 LangGraph node
events MUST map onto these wire types (see docs/v1/architecture.md §5);
no new SSE event_type may reach the frontend without an ADR.

Only human-language labels/detail are persisted — never system prompts,
bundles, secrets, or tracebacks (turns.py rule, preserved here).
"""
import json
import re
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

# Every event_type the current transport can emit (turns.py _TOOL_LABELS +
# chat.py terminal/close events). Frozen superset; removal needs an ADR.
LEGACY_EVENT_TYPES = (
    "turn_started",
    "provider_selected",
    "context_started",
    "context_completed",
    "rag_started",
    "rag_completed",
    "web_started",
    "web_completed",
    "web_source",
    "instagram_provider_started",
    "instagram_provider_completed",
    "delegation_started",
    "state_read",
    "tool_completed",
    "tool_failed",
    "job_created",
    "approval_required",
    "synthesis_started",
    "assistant_delta",
    "model_delta",
    "assistant_completed",
    "model_completed",
    "turn_completed",
    "turn_failed",
    "turn_closed",
    "stream_end",
    # --- execution-tree block (DEV-008-SKILLS-OPS §1.3.1) ---
    # 11 NEW names. The mission's other 4 (tool_completed, tool_failed,
    # approval_required, synthesis_started) are already members above; they
    # were among the 13 that never reached the SPA, and W7 closes that gap.
    # `turn_summary` is deliberately NOT here: it is emitted straight into
    # repos.ExecutionEvents.insert, bypassing GraphExecutionEvent, and the
    # chat.py filter drops it. It stays out of band (§1.3.1, §8.3).
    "account_manager_started",
    "task_planned",
    "employee_queued",
    "employee_started",
    "employee_completed",
    "employee_failed",
    "skill_selected",
    "skill_loaded",
    "tool_started",
    "evidence_added",
    "synthesis_completed",
)

TERMINAL_EVENT_TYPES = ("turn_completed", "turn_failed")
_EVENT_METADATA_MAX_BYTES = 4000


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _compact_model_telemetry(value: Any) -> dict[str, Any]:
    """Keep compact generation fields while leaving absent values absent."""
    if not isinstance(value, dict):
        return {}
    keys = (
        "provider", "requested_model", "actual_model", "model", "attempts",
        "status", "status_code", "latency_ms", "input_tokens", "cached_tokens",
        "output_tokens", "reasoning_tokens", "total_tokens", "route_mode",
        "route_reason", "call_id",
    )
    return {key: value[key] for key in keys if key in value}


def _serialize_event_metadata(event_type: str, metadata: dict[str, Any]) -> str:
    """Serialize bounded, valid JSON; prioritize completion telemetry over text.

    Event metadata used to be sliced as a string, which could turn a valid
    object into invalid JSON. Completion text already has its own `detail`
    field, so the compact completion record reserves space for telemetry.
    """
    clean = sanitize_metadata(metadata)
    # Preserve the canonical compact retrieval contract before applying the
    # DEV-024 bounded, valid-JSON event serializer.
    if "retrieval_telemetry" in clean:
        clean["retrieval_telemetry"] = compact_retrieval_telemetry(
            clean.get("retrieval_telemetry"))
    try:
        full = _json_bytes(clean)
    except (TypeError, ValueError, OverflowError):
        clean = {}
        full = b"{}"
    if len(full) <= _EVENT_METADATA_MAX_BYTES:
        return full.decode("utf-8")

    truncated = True
    if event_type == "model_completed":
        compact: dict[str, Any] = {}
        for key, limit in (("turn_id", 128), ("call_id", 128), ("route", 300)):
            if key in clean:
                value = clean[key]
                compact[key] = value[:limit] if isinstance(value, str) else value
        telemetry = _compact_model_telemetry(clean.get("telemetry"))
        retrieval = compact_retrieval_telemetry(clean.get("retrieval_telemetry"))
        if telemetry:
            compact["telemetry"] = telemetry
        if retrieval:
            compact["retrieval_telemetry"] = retrieval
        compact["telemetry_truncated"] = truncated
        # Preserve remaining safe scalar metadata only if it fits; large text
        # and diagnostic values are intentionally omitted from this event.
        for key, value in clean.items():
            if key in compact or key in {"text", "telemetry", "retrieval_telemetry"}:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                compact[key] = value
        candidate = _json_bytes(compact)
        if len(candidate) <= _EVENT_METADATA_MAX_BYTES:
            return candidate.decode("utf-8")
        # Nonessential metadata can be dropped, but the wire remains explicit.
        compact = {key: value for key, value in compact.items()
                   if key in {"turn_id", "call_id", "route", "telemetry",
                              "retrieval_telemetry", "telemetry_truncated"}}
        candidate = _json_bytes(compact)
        while len(candidate) > _EVENT_METADATA_MAX_BYTES:
            retrieval_meta = compact.get("retrieval_telemetry", {})
            selected_ids = retrieval_meta.get("selected_chunk_ids")
            source_ids = retrieval_meta.get("source_file_ids")
            if isinstance(selected_ids, list) and selected_ids:
                selected_ids.pop()
            elif isinstance(source_ids, list) and source_ids:
                source_ids.pop()
            elif "selected_chunk_ids" in retrieval_meta:
                retrieval_meta.pop("selected_chunk_ids")
            elif "source_file_ids" in retrieval_meta:
                retrieval_meta.pop("source_file_ids")
            elif compact.get("telemetry"):
                compact["telemetry"].pop(next(reversed(compact["telemetry"])))
            else:
                break
            candidate = _json_bytes(compact)
        return candidate.decode("utf-8")

    # Other event classes are already designed to stay small. If a caller
    # exceeds the bound, drop largest values first while keeping valid JSON.
    compact = dict(clean)
    compact["telemetry_truncated"] = True
    candidate = _json_bytes(compact)
    while len(candidate) > _EVENT_METADATA_MAX_BYTES:
        candidates = [key for key in compact if key != "telemetry_truncated"]
        if not candidates:
            return '{"telemetry_truncated":true}'
        key = max(candidates, key=lambda k: len(_json_bytes(compact[k])))
        del compact[key]
        candidate = _json_bytes(compact)
    return candidate.decode("utf-8")

# The visual status enum the UI must render (DEV-008-SKILLS-OPS §1.3.4).
# `meta.status` on a tree event is always exactly one of these, uppercase.
# CANCELLED has no user-facing cancel surface in this run — the enum member
# is mandatory in the render map regardless (§1.3.4, honest limitation).
TREE_STATUSES = (
    "QUEUED", "RUNNING", "COMPLETE", "FAILED",
    "RETRYING", "WAITING_FOR_APPROVAL", "CANCELLED",
)

# Metadata keys that must never be persisted or streamed (chat.py _safe_meta
# rule: prompt/system stripped; extended here to secrets/tracebacks).
# The original 20 entries are preserved verbatim so no existing sanitisation
# test can weaken; the chain-of-thought block is additive (DEV-008-SKILLS-OPS
# §1.3.5).
_FORBIDDEN_META_KEYS = frozenset({
    "prompt", "system", "secret", "api_key", "token", "access_token",
    "refresh_token", "authorization", "password", "credential", "traceback",
    "chain_of_thought", "reasoning_content", "exception", "stderr", "stdout",
    "raw", "debug", "env", "environment", "provider_url", "base_url",
    "reasoning", "reasoning_trace", "thoughts", "thinking", "scratchpad",
    "deliberation", "monologue", "internal_notes", "plan_of_thought",
    "cot", "rationale",
})

# Exact-match alone let `reasoning_trace`, `thought_steps`, `cot_chain`,
# `raw_model_output` and `llm_system` through. The filter is now
# exact + prefix + suffix + env-symbol (§1.3.5, frozen).
_FORBIDDEN_META_PREFIXES = (
    "reasoning", "thought", "thinking", "scratchpad",
    "deliberat", "monologue", "internal_note",
    "cot_", "raw_",
)
_FORBIDDEN_META_SUFFIXES = (
    "_prompt", "_system", "_raw", "_secret", "_token",
    "_api_key", "_password", "_credential",
)

_REDACTED = "[REDACTED]"
_GENERIC_ERROR = "The request could not be completed."
_SECRET_VALUE_PATTERNS = (
    re.compile(
        r"(?i)\b(?:[A-Z][A-Z0-9_]*(?:API_KEY|ACCESS_TOKEN|AUTH_TOKEN|REFRESH_TOKEN|SECRET|PASSWORD|CREDENTIAL)[A-Z0-9_]*|password|passwd|secret|token|api[_-]?key|access[_-]?token|refresh[_-]?token|authorization)\s*[:=]\s*[^\s,;)\]}]+"
    ),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{8,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"(?i)\bvault://[^\s\"']+"),
    re.compile(r"(?i)\b(?:xox[bap]-[A-Za-z0-9-]{8,}|glpat-[A-Za-z0-9_-]{10,})\b"),
    re.compile(r"(?i)-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
_PROVIDER_HOSTS = frozenset({
    "api.openai.com",
    "openrouter.ai",
    "api.anthropic.com",
    "api.apify.com",
    "api.brightdata.com",
    "generativelanguage.googleapis.com",
    "graph.facebook.com",
    "localhost",
    "127.0.0.1",
    "::1",
})
_ENV_SYMBOLS = frozenset({
    "AI_RUNTIME",
    "MANAGER_PROVIDER",
    "MANAGER_MODE",
    "MANAGER_PROVIDER_PRIORITY",
    "LLM_PROVIDER",
    "OMOS_CLOUD_PROVIDER",
    "ROUTER_MODE",
    "OPENROUTER_TEST_MODEL",
    "OPENROUTER_CATALOG_TTL_S",
    "LLAMA_BASE_URL",
    "LLAMA_ADAPTER",
    "LLAMA_QUANT",
    "LLAMA_MODEL",
    "LLAMA_N_CTX",
    "LLAMA_N_GPU_LAYERS",
    "LLAMA_TIMEOUT_S",
    "LLAMA_HEALTH_TIMEOUT_S",
    "VISION_LOCAL_WEIGHTS",
    "IG_BROWSER_PROFILE",
    "MAX_AGENT_CONCURRENCY",
    "OMOS_DATA_DIR",
    "OMOS_CREDENTIALS_DIR",
    "CHROMA_ENABLED",
    "COMSPEC",
})
_URL_PATTERN = re.compile(r"(?i)\b(?:https?|wss?)://[^\s\"'<>]+")
_PROVIDER_URL_HOST_PATTERN = re.compile(
    r"(?i)\b(?:api\.openai\.com|openrouter\.ai|api\.anthropic\.com|api\.apify\.com|api\.brightdata\.com|generativelanguage\.googleapis\.com|graph\.facebook\.com|localhost|127\.0\.0\.1|\[::1\])\b"
)
_WINDOWS_PATH_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:[A-Z]:[\\/]|\\\\[^\\/\s]+[\\/])[^\s\"'<>]*"
)
_UNIX_PATH_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9_:/])/(?:[^\s/\\\"'<>]+/)+[^\s/\\\"'<>]+"
)
_UNIX_SAFE_ROUTE_SEGMENTS = frozenset({
    "api", "app", "apps", "approvals", "assets", "brain", "campaign", "campaigns",
    "chat", "companies", "docs", "files", "graph", "health", "jobs", "knowledge",
    "openapi", "projects", "redoc", "results", "settings", "static", "tasks",
})
_FILE_URL_PATTERN = re.compile(r"(?i)\bfile://[^\s\"'<>]+")
_STACK_FRAME_PATTERN = re.compile(
    r"(?m)^\s*(?:File\s+[^\r\n]+|at\s+[^\r\n]+:\d+(?::\d+)?\)?)\s*$"
)
_EXCEPTION_SIGNATURE_PATTERN = re.compile(
    r"(?i)\b(?:[A-Za-z_][\w.]*(?:Error|Exception)|Exception)\s*:"
)
_TRACEBACK_PATTERN = re.compile(r"(?i)\bTraceback\s*\(most recent call last\)\s*:")
_EXCEPTION_CHAIN_PATTERN = re.compile(
    r"(?i)\b(?:during handling of the above exception|the above exception was the direct cause)\b"
)
_DIAGNOSTIC_PHRASE_PATTERN = re.compile(
    r"(?i)\b(?:provider|request|connection|upstream|endpoint|api|service|model|tool|worker|process)\b[^\r\n]{0,120}?\b(?:failed|failure|unreachable|error|exception|timeout|timed out)\b"
)
_ENV_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b([A-Z0-9]+(?:[-_.][A-Z0-9]+)*)\s*[:=]\s*[^\s,;)\]}]+"
)
_ENV_SYMBOL_PATTERN = re.compile(
    r"(?i)\b(?:[A-Z0-9]+(?:[-_.][A-Z0-9]+)+|COMSPEC)\b"
)

# DEV-008-SKILLS-OPS remediation (QA MEDIUM-1). `ai-seo` is a pinned upstream
# marketing-playbook id, not a secret-shaped construct; the env-symbol filter
# redacted exactly this one of the 50 pinned skill ids on the SSE wire
# ("Using ai-seo" -> "Using [REDACTED]") because it normalises to AI_SEO.
# The persisted row keeps the real value; only the wire projection lost it.
# The cutout is therefore a static, pinned allowlist of the AI_-prefixed
# upstream ids — narrower than "allow anything matching a registry name" and
# keeps this contract free of product-package imports (events.py holds events
# and the app's dependency on the skills package apart). Eligibility is
# three-condition: the value must match ^[a-z0-9][a-z0-9-]*$ (lowercase
# skill-id shape), its canonical form must be AI_-prefixed (so the cutout can
# never whitewash anything else), and the id must be a member here. Measured
# distribution over .agents/skills/ (asserted in
# test_dev008so_event_catalog.py, derived from the real library): exactly one
# of the 50 pinned ids normalises to an AI_ prefix — `ai-seo`; its closest
# neighbours `ads` and `image` carry no separator, so they never matched the
# env-symbol filter in the first place. Everything else is unchanged:
# AI_RUNTIME-style product env names and every other prefix still redact.
AI_PREFIXED_SKILL_IDS = ("ai-seo",)


def _canonical_symbol(name: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", str(name or "").strip()).upper().strip("_")


def _is_pinned_ai_prefixed_skill_id(name: str) -> bool:
    """The narrow ai-seo cutout (see AI_PREFIXED_SKILL_IDS). False positives
    here widen the wire surface, so every condition is an AND: the value must
    match ^[a-z0-9][a-z0-9-]*$ (the same shape over the _-form after the
    hyphen->underscore normalisation), its canonical form must be AI_-prefixed
    (so the cutout can never whitewash anything else), and it must be a member
    of the pinned allowlist."""
    raw = str(name or "").strip().lower().replace("-", "_")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_]*", raw):
        return False
    return raw.upper() in tuple(sid.replace("-", "_").upper()
                                for sid in AI_PREFIXED_SKILL_IDS)


def _is_env_symbol(name: str) -> bool:
    normalized = _canonical_symbol(name)
    if not normalized:
        return False
    if _is_pinned_ai_prefixed_skill_id(normalized):
        return False
    if normalized in _ENV_SYMBOLS:
        return True
    if "_" not in normalized:
        return False
    if normalized.endswith((
        "_API_KEY", "_ACCESS_TOKEN", "_AUTH_TOKEN", "_REFRESH_TOKEN",
        "_SECRET", "_PASSWORD", "_CREDENTIAL", "_TOKEN",
    )):
        return True
    return normalized.startswith((
        "AI_", "OMOS_", "LLM_", "LLAMA_", "OPENAI_", "OPENROUTER_",
        "APIFY_", "BRIGHTDATA_", "META_", "MANAGER_", "ROUTER_",
        "VISION_", "IG_", "W8_", "MAX_", "CHROMA_",
    ))


def _redact_env_match(match: re.Match[str]) -> str:
    name = match.group(1) if match.lastindex else match.group(0)
    return _REDACTED if _is_env_symbol(name) else match.group(0)


def _redact_env_symbol(match: re.Match[str]) -> str:
    return _REDACTED if _is_env_symbol(match.group(0)) else match.group(0)


def _unix_path_is_sensitive(value: str) -> bool:
    parts = [part for part in str(value or "").strip("/").split("/") if part]
    if len(parts) < 2:
        return False
    if parts[0].lower() in _UNIX_SAFE_ROUTE_SEGMENTS:
        return False
    return True


def _redact_unix_path(match: re.Match[str]) -> str:
    return _REDACTED if _unix_path_is_sensitive(match.group(0)) else match.group(0)


def _url_is_sensitive(value: str) -> bool:
    raw = str(value or "").rstrip(".,;:!?)]}")
    try:
        parsed = urlparse(raw)
        host = str(parsed.hostname or "").lower().rstrip(".")
        path = str(parsed.path or "").lower()
    except ValueError:
        return True
    if not host:
        return True
    if host in _PROVIDER_HOSTS or _PROVIDER_URL_HOST_PATTERN.search(host):
        return True
    if any(token in host for token in (
        "provider", "openai", "openrouter", "anthropic", "apify",
        "brightdata", "facebook", "googleapis",
    )):
        return True
    if host.startswith(("10.", "192.168.")) or host.startswith("169.254."):
        return True
    return any(token in path for token in (
        "/v1/", "/v2/", "/v3/", "/chat", "/completions", "/endpoint",
        "/provider", "/api/",
    ))


def _diagnostic_start(text: str) -> int | None:
    starts: list[int] = []
    for pattern in (
        _TRACEBACK_PATTERN,
        _STACK_FRAME_PATTERN,
        _EXCEPTION_SIGNATURE_PATTERN,
        _EXCEPTION_CHAIN_PATTERN,
        _DIAGNOSTIC_PHRASE_PATTERN,
    ):
        match = pattern.search(text)
        if match:
            starts.append(match.start())
    for match in _URL_PATTERN.finditer(text):
        if _url_is_sensitive(match.group(0)):
            starts.append(match.start())
    for pattern in (_WINDOWS_PATH_PATTERN, _FILE_URL_PATTERN):
        match = pattern.search(text)
        if match:
            starts.append(match.start())
    for match in _UNIX_PATH_PATTERN.finditer(text):
        if _unix_path_is_sensitive(match.group(0)):
            starts.append(match.start())
    return min(starts) if starts else None


def _project_diagnostic(text: str) -> str:
    start = _diagnostic_start(text)
    if start is None:
        return text
    prefix = text[:start].rstrip()
    prefix = re.sub(
        r"(?i)\s*(?:the\s+)?(?:provider|request|connection|upstream|endpoint|api|service|model|tool|worker|process)\s*(?:failed|failure|error|exception|unavailable|timeout|timed out)?\s*[:\-]?\s*$",
        "",
        prefix,
    ).rstrip()
    return f"{prefix} {_GENERIC_ERROR}".strip() if prefix else _GENERIC_ERROR


def sanitize_user_text(value: Any, max_length: int = 6000, preserve_urls: bool = False) -> str:
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    if not text:
        return ""
    text = _project_diagnostic(text)
    text = _ENV_ASSIGNMENT_PATTERN.sub(_redact_env_match, text)
    for pattern in _SECRET_VALUE_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    text = _FILE_URL_PATTERN.sub(_REDACTED, text)
    text = _WINDOWS_PATH_PATTERN.sub(_REDACTED, text)
    text = _UNIX_PATH_PATTERN.sub(_redact_unix_path, text)
    text = _STACK_FRAME_PATTERN.sub(_REDACTED, text)
    text = _EXCEPTION_CHAIN_PATTERN.sub(_REDACTED, text)
    text = _ENV_SYMBOL_PATTERN.sub(_redact_env_symbol, text)
    diagnostic_context = bool(_diagnostic_start(text))
    text = _URL_PATTERN.sub(
        lambda match: match.group(0) if preserve_urls and not _url_is_sensitive(match.group(0)) and not diagnostic_context else (_REDACTED if _url_is_sensitive(match.group(0)) or diagnostic_context else match.group(0)),
        text,
    )
    limit = max(0, int(max_length))
    return text[:limit]


def safe_error_message(
    value: Any,
    fallback: str = _GENERIC_ERROR,
    *,
    force_generic: bool = False,
) -> str:
    if force_generic:
        return fallback
    if isinstance(value, BaseException):
        original = str(value)
        if "NO_PROJECT_SCOPE" in original and _diagnostic_start(original) is None:
            return sanitize_user_text(original)
        return fallback
    original = value if isinstance(value, str) else ("" if value is None else str(value))
    if not original.strip() or _diagnostic_start(original) is not None:
        return fallback
    projected = sanitize_user_text(original)
    return projected or fallback


def _sanitize_meta_value(value: Any) -> Any:
    if isinstance(value, BaseException):
        return safe_error_message(value)
    if isinstance(value, str):
        return sanitize_user_text(value)
    if isinstance(value, dict):
        return sanitize_metadata(value)
    if isinstance(value, (list, tuple)):
        return [_sanitize_meta_value(item) for item in value]
    return value


def _forbidden_meta_key(key: Any) -> bool:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", str(key or "").strip()).lower()
    if normalized in _FORBIDDEN_META_KEYS:
        return True
    if normalized.startswith(_FORBIDDEN_META_PREFIXES):
        return True
    if normalized.endswith(_FORBIDDEN_META_SUFFIXES):
        return True
    return _is_env_symbol(normalized)


def sanitize_metadata(meta: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    return {
        key: _sanitize_meta_value(value)
        for key, value in meta.items()
        if not _forbidden_meta_key(key)
    }


def compact_retrieval_telemetry(value: Any) -> dict[str, Any]:
    """Project the small, content-free retrieval contract used by acceptance.

    Missing fields stay missing: an unavailable historical counter must not be
    turned into a measured zero. Legacy aliases are read for compatibility.
    """
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    mode = value.get("retrieval_mode", value.get("mode", value.get("search_mode")))
    if isinstance(mode, str):
        result["retrieval_mode"] = sanitize_user_text(mode, 32)
    for key in ("lexical_hits", "vector_hits", "fused_hits"):
        if key not in value or value[key] is None:
            continue
        try:
            count = int(value[key])
        except (TypeError, ValueError, OverflowError):
            continue
        if count >= 0:
            result[key] = count
    chunks = value.get("selected_chunk_ids")
    files = value.get("source_file_ids")
    if chunks is None and isinstance(value.get("selected_chunks"), list):
        chunks = [item.get("chunk_id") for item in value["selected_chunks"]
                  if isinstance(item, dict)]
    if files is None and isinstance(value.get("selected_chunks"), list):
        files = [item.get("file_id") for item in value["selected_chunks"]
                 if isinstance(item, dict)]
    if isinstance(chunks, list):
        result["selected_chunk_ids"] = [sanitize_user_text(x, 160) for x in chunks[:20]
                                        if isinstance(x, str) and x]
    if isinstance(files, list):
        result["source_file_ids"] = [sanitize_user_text(x, 160) for x in files[:20]
                                     if isinstance(x, str) and x]
    if value.get("telemetry_truncated") is True:
        result["telemetry_truncated"] = True
    return result


def _bounded_metadata(meta: dict[str, Any], limit: int = 4000) -> str:
    """Serialize metadata without cutting JSON or dropping retrieval evidence."""
    clean = sanitize_metadata(meta)
    # Retrieval telemetry is the compact acceptance contract and gets first
    # claim on the row budget. Provider telemetry follows, then other fields.
    priority: dict[str, Any] = {}
    retrieval = compact_retrieval_telemetry(clean.get("retrieval_telemetry"))
    if retrieval:
        priority["retrieval_telemetry"] = retrieval
    if "telemetry" in clean:
        priority["telemetry"] = clean["telemetry"]
    base = dict(priority)
    rest = {k: v for k, v in clean.items() if k not in priority}
    candidate = {**base, **rest}
    encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) <= limit:
        return encoded
    # Start with protected fields and add optional fields whole while they fit.
    candidate = dict(base)
    candidate["telemetry_truncated"] = True
    for key, value in rest.items():
        trial = {**candidate, key: value}
        if len(json.dumps(trial, ensure_ascii=False, separators=(",", ":"))) <= limit:
            candidate = trial
    encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
    # Provider telemetry can itself be unusually large. Keep it only when it
    # fits beside the acceptance-critical retrieval snapshot.
    if len(encoded) > limit:
        candidate.pop("telemetry", None)
        encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
    return encoded


def _metadata_from_row(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        raw = row.get("metadata")
        if raw is None:
            raw = row.get("metadata_json")
    else:
        raw = getattr(row, "metadata", None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except (TypeError, ValueError):
            raw = {"telemetry_truncated": True}
    return raw if isinstance(raw, dict) else {}


def project_event_boundary(row: Any, *, event_type: str = "") -> dict[str, Any]:
    if isinstance(row, dict):
        kind = str(event_type or row.get("event_type", "") or "")
        label = row.get("label", "")
        detail = row.get("detail", "")
    else:
        kind = str(event_type or getattr(row, "event_type", "") or "")
        label = getattr(row, "label", "")
        detail = getattr(row, "detail", "")
    return {
        "label": sanitize_user_text(label, 300),
        "detail": sanitize_user_text(detail, 6000, preserve_urls=kind == "web_source"),
        "meta": sanitize_metadata(_metadata_from_row(row)),
    }


class GraphExecutionEvent(BaseModel):
    """One lifecycle event. Maps 1:1 onto an execution_events row."""

    project_id: str = Field(min_length=1)
    conversation_id: str = ""
    turn_id: str = ""
    job_id: str = ""
    event_type: str
    label: str = Field(default="", max_length=300)
    detail: str = Field(default="", max_length=6000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""

    @field_validator("event_type")
    @classmethod
    def _known_wire_type(cls, v: str) -> str:
        if v not in LEGACY_EVENT_TYPES:
            raise ValueError(f"unknown SSE wire event_type: {v!r}")
        return v

    @field_validator("label", mode="before")
    @classmethod
    def _clean_label(cls, v: Any) -> str:
        return sanitize_user_text(v, 300)

    @field_validator("detail", mode="before")
    @classmethod
    def _clean_detail(cls, v: Any) -> str:
        return sanitize_user_text(v, 6000)

    @field_validator("metadata", mode="before")
    @classmethod
    def _clean_metadata(cls, v: Any) -> dict[str, Any]:
        return sanitize_metadata(v)

    @property
    def is_terminal(self) -> bool:
        return self.event_type in TERMINAL_EVENT_TYPES

    def to_row(self) -> dict[str, Any]:
        """Shape accepted by repos.ExecutionEvents.insert (minus id)."""
        import json as _json

        return {
            "project_id": self.project_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "job_id": self.job_id,
            "event_type": self.event_type,
            "label": self.label[:300],
            "detail": self.detail[:6000],
            "metadata_json": _serialize_event_metadata(self.event_type, self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "GraphExecutionEvent":
        import json as _json

        try:
            meta = _json.loads(row.get("metadata_json", "") or "{}")
        except ValueError:
            # The old writer sliced JSON at 4,000 characters. Preserve that
            # data-loss fact for replay consumers instead of silently treating
            # the malformed row as complete empty metadata.
            meta = {"telemetry_truncated": True}
        return cls(
            project_id=row.get("project_id", ""),
            conversation_id=row.get("conversation_id", ""),
            turn_id=row.get("turn_id", ""),
            job_id=row.get("job_id", "") or "",
            event_type=row.get("event_type", ""),
            label=row.get("label", ""),
            detail=row.get("detail", ""),
            metadata=meta if isinstance(meta, dict) else {},
            created_at=row.get("created_at", ""),
        )

    def to_sse(self, id: int) -> dict[str, Any]:
        """Frozen SSE envelope from chat.py: id/event/data{label,detail,meta}."""
        data = project_event_boundary(self)
        safe_meta = data["meta"]
        if self.event_type == "model_delta":
            try:
                sequence = int(safe_meta.get("sequence", 0) or 0)
            except (TypeError, ValueError):
                sequence = 0
            data.update({
                "turn_id": sanitize_user_text(self.turn_id, 128),
                "call_id": sanitize_user_text(str(safe_meta.get("call_id", "")), 128),
                "sequence": sequence,
                "delta": sanitize_user_text(str(safe_meta.get("delta", data["detail"])), 6000),
            })
        elif self.event_type == "model_completed":
            data.update({
                "turn_id": sanitize_user_text(self.turn_id, 128),
                "call_id": sanitize_user_text(str(safe_meta.get("call_id", "")), 128),
                "telemetry": safe_meta.get("telemetry", {}),
                "retrieval_telemetry": safe_meta.get("retrieval_telemetry", {}),
            })
        return {
            "id": id,
            "event": self.event_type,
            "data": data,
        }
