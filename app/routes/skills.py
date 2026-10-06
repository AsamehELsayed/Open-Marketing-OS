"""The marketing-playbook registry API (DEV-008-SKILLS-OPS W6).

Three endpoints, all read or toggle-only::

    GET  /api/skills                        the registry + validation counts
    POST /api/skills/{skill_id}/enabled     toggle one playbook on or off
    GET  /api/skills/manifest               the generated pin document

Skills are **knowledge**, tools are **action**. Nothing in this module imports
``app.services.tools`` and nothing here can execute a playbook -- a
``MarketingSkillRecord`` has no ``handler``, no ``parameters`` and no
``permission_level``, and that separation is the reason a vendored playbook can
never acquire the power to spend money. The two vocabularies are kept apart on
purpose, so this file stays a pure read/toggle surface.

Two things this module is careful about.

**The library is unavailable, not empty.** When no library root resolves, the
loader raises :class:`SkillRegistryUnavailable`. That is reported as ``503``
with a named reason, never as ``200`` with an empty list: a client that renders
"0 skills" from a broken install looks identical to a client that legitimately
has no skills, and the founder would have no way to tell a packaging fault from
a real state. (Chat itself is *not* allowed to fail this way -- the router
proceeds skill-free with ``registry-unavailable`` -- but an API that reports a
fault as data is how a fault becomes permanent.)

**No absolute path ever reaches the wire.** A skill's ``path`` is the relative
``.agents/skills/<skill_id>`` by construction, and the loader's
``to_dict()`` reports the library location as the relative label
``.agents/skills`` rather than the absolute root it actually read.
``app.contracts.events.sanitize_user_text`` redacts absolute Windows and Unix
paths, so a leaked one would arrive as ``[REDACTED]`` -- a field that looks
populated and is useless, which is worse than an honest relative label.
``test_dev008so_skill_routes.py`` asserts this on the real response bytes.

Every response uses the ``{"ok": true, "data": ...}`` envelope the SPA client
unwraps (``frontend/src/api/client.ts``).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import deps
from app.services.config_service import ConfigService
from app.services.skills import (
    SETTINGS_DISABLED_KEY,
    SkillRegistryUnavailable,
    load_registry,
)
from app.services.skills.registry import read_manifest

router = APIRouter(prefix="/api", tags=["skills"])


def _ok(data):
    """The SPA envelope. Every response in this module goes through it."""
    return {"ok": True, "data": data}


def _load_or_503():
    """The live registry, or an honest 503.

    ``disabled`` is left unset so the loader reads ``skills_disabled`` from the
    database on this request -- that is what makes a Settings toggle take effect
    on the next read without a reload, and it is why no cache is kept here.
    """
    try:
        return load_registry()
    except SkillRegistryUnavailable as exc:
        raise HTTPException(
            status_code=503,
            # ``type(exc).__name__`` rather than ``str(exc)``: the exception text
            # lists every candidate root it tried, and those are absolute paths
            # on this platform. The named class says what is wrong without
            # shipping the filesystem layout of the install.
            detail=("the marketing-skills library is unavailable: "
                    + type(exc).__name__),
        ) from None


class SkillEnabledBody(BaseModel):
    """``{"enabled": true|false}``. Required, so a malformed toggle is a 422."""

    enabled: bool


@router.get("/skills")
def skills_list():
    """The registry, its validation counts and the library checksum.

    Every count in the payload is measured by the loader on this request. The
    client never recomputes a total and never assumes a library size, so a
    re-pin needs no frontend change and no code change on this side.
    """
    return _ok(_load_or_503().to_dict())


@router.get("/skills/manifest")
def skills_manifest():
    """The generated pin document: upstream tag, commit, licence, digests.

    Read straight off disk rather than regenerated -- this endpoint reports what
    was *recorded*, and ``python -m app.services.skills.manifest --verify`` is
    what decides whether the recording still matches the tree. An endpoint that
    silently re-derived it would make drift undetectable.
    """
    document = read_manifest()
    if document is None:
        raise HTTPException(
            status_code=404,
            detail="the skills manifest is absent or unreadable; "
                   "run python -m app.services.skills.manifest --write",
        )
    return _ok(document)


@router.post("/skills/{skill_id}/enabled")
def set_skill_enabled(skill_id: str, body: SkillEnabledBody):
    """Enable or disable one playbook, and say so.

    The flag lives in the ``skills_disabled`` setting as a comma-separated list
    of skill ids, written through :class:`ConfigService` on the request's own
    connection -- the same key the loader reads, so the toggle is visible on the
    very next read and the next turn.

    Fail-closed on two counts: an unknown ``skill_id`` is a ``404`` rather than a
    silently-ignored write, and a non-boolean ``enabled`` never reaches the
    setting at all (the model rejects it with a ``422``). A toggle that quietly
    did nothing is worse than one that refused.
    """
    registry = _load_or_503()
    if skill_id not in registry:
        raise HTTPException(status_code=404, detail="unknown skill")

    with deps.get_db() as conn:
        raw = ConfigService.get_setting(SETTINGS_DISABLED_KEY, "", conn=conn)
        disabled = {part.strip() for part in str(raw or "").split(",") if part.strip()}
        if body.enabled:
            disabled.discard(skill_id)
        else:
            disabled.add(skill_id)
        # Sorted so the stored value is a function of the set, not of the order
        # toggles happened to arrive in. Two clients that toggle the same two
        # skills in opposite orders must not leave two different strings behind.
        ConfigService.set_setting(
            SETTINGS_DISABLED_KEY, ",".join(sorted(disabled)), conn=conn)

    return _ok({"skill_id": skill_id, "enabled": body.enabled, "status": "updated"})
