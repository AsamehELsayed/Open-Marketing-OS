"""Skill frontmatter parser: PyYAML first, stdlib fallback (DEV-008-SKILLS-OPS).

Why a fallback when PyYAML is a declared, locked, shipped dependency
--------------------------------------------------------------------
The mission premise was "the repo has no PyYAML guarantee". That premise is
false -- ``requirements.txt:37`` declares ``PyYAML>=6.0``,
``requirements-lock.txt:37`` pins ``PyYAML==6.0.3``, ``NOTICE`` records it as
MIT, and ``app/tests/test_w5_router_telemetry.py:284`` *asserts* it is declared.
So the fallback is not here because PyYAML is missing. It is here for two real
reasons:

1. ``packaging/omos.spec:100`` lists ``yaml.cython`` in ``HIDDEN_IMPORTS`` but
   **not** ``yaml``. A frozen build that lost PyYAML would fail at import time
   with an opaque error, and this is the load-bearing feature of the run. A
   stdlib path removes that whole class of failure.
2. The repo already owns this exact pattern in shipped code
   (``app/services/llm/model_router.py:139 _minimal_mapping``), so this is the
   house style rather than a new invention.

The stdlib reader is deliberately **total only over the measured corpus**: three
top-level keys, every value a single-line scalar, one level of nesting. Anchors,
flow collections, block scalars, multi-line plain scalars and deeper nesting all
raise :class:`SkillParseError`, because silently mis-parsing a playbook is worse
than refusing it. ``test_dev008so_skill_parse.py`` proves the equivalence on every
vendored file rather than asserting it.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterator

from .records import (
    DESCRIPTION_MAX_CHARS,
    TRIGGER_MAX_CHARS,
    normalize_text,
)

#: Parser names reported by :func:`parse_frontmatter`.
PARSER_NAMES = ("pyyaml", "stdlib")

_DELIMITER = "---"

#: Section locator. Case-insensitive on both words because upstream is not
#: self-consistent (``marketing-plan/SKILL.md:262`` writes ``## Related skills``).
_RELATED_HEADING = re.compile(r"^##\s*[Rr]elated\s+[Ss]kills\s*$", re.M)

#: Body of a Related Skills section runs to the next H2, or to EOF.
_NEXT_H2 = re.compile(r"^##\s", re.M)

#: The three Related Skills shapes found in the vendored corpus, as a UNION.
#: Not first-match: a single-shape parser silently loses ``marketing-plan``
#: (backticked slugs inside bold) and ``customer-research`` (a markdown table).
#: Measured over the real tree: shape 0 -> 1 file, shape 1 -> 1 file,
#: shape 2 -> 46 files, 2 files with no section at all.
_RELATED_PATTERNS = (
    re.compile(r"-\s*\*\*`([a-z0-9][a-z0-9-]*)`\*\*"),
    re.compile(r"\|\s*`([a-z0-9][a-z0-9-]*)`\s*\|"),
    re.compile(r"-\s*\*\*([a-z0-9][a-z0-9-]*)\*\*"),
)

#: Description-tail cross-reference: "For page-level CRO, see cro."
_TAIL_REF = re.compile(r"\bsee\s+([a-z0-9][a-z0-9\-]{1,40})", re.I)

_DESCRIPTION_LINE = re.compile(r"^description:[ \t]*(.*)$")
_NAME_LINE = re.compile(r"^name:[ \t]*(.*)$")
_VERSION_LINE = re.compile(r"^[ \t]+version:[ \t]*(.*)$")

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

_SKILL_ID = re.compile(r"^[a-z0-9-]+$")

_WHITESPACE = re.compile(r"\s+")


class SkillParseError(Exception):
    """A skill could not be read as a playbook.

    ``reason_code`` is always one of ``records.REASON_CODES`` so a failure is
    attributable to a named defect rather than surfacing as a stack trace.
    """

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        self.detail = detail
        message = reason_code if not detail else f"{reason_code}: {detail}"
        super().__init__(message)


# --------------------------------------------------------------------- #
# frontmatter extraction                                                 #
# --------------------------------------------------------------------- #


def split_frontmatter(text: str) -> tuple[list[str], str]:
    """Return ``(frontmatter_lines, body)`` for a ``SKILL.md`` document.

    The document must open with the exact line ``---`` after an optional BOM.
    The closing delimiter is the first subsequent line equal to ``---`` after
    stripping trailing spaces/tabs/CR. Leading and trailing blank lines inside
    the block are dropped.
    """
    body = text.lstrip("\ufeff")
    lines = body.split("\n")
    if not lines or lines[0].rstrip(" \t\r") != _DELIMITER:
        raise SkillParseError(
            "FRONTMATTER_UNDELIMITED", "document does not open with '---'"
        )

    closing = None
    for index in range(1, len(lines)):
        if lines[index].rstrip(" \t\r") == _DELIMITER:
            closing = index
            break
    if closing is None:
        raise SkillParseError("FRONTMATTER_UNDELIMITED", "no closing '---'")

    return _drop_blank_edges(lines[1:closing]), "\n".join(lines[closing + 1:])


def _drop_blank_edges(lines: list[str]) -> list[str]:
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


# --------------------------------------------------------------------- #
# the frozen two-style description split                                   #
# --------------------------------------------------------------------- #


def unquote_description(raw: str) -> str:
    """Turn a raw ``description:`` value into its text.

    Applied to the **raw** line, before any quote stripping, because the two
    styles in the corpus are distinguished exactly by the outer quotes:

    * double-quoted -- a YAML double-quoted scalar, decoded with ``json.loads``
      (YAML's double-quoted escape set is a superset of JSON's for the
      characters actually present here -- measured: 28/28 decode).
    * plain -- everything after the **first** ``": "`` (colon-space) or a
      trailing ``":"``.

    Known defect in the frozen rule (reported, not silently fixed): the two
    terminators are written with their surrounding double-quotes, so they are 4
    and 3 characters respectively -- ``": "`` is quote/colon/space/quote and
    ``":"`` is quote/colon/quote. Dropping ``len(term) - 1`` characters from the
    first is exact, but the second leaves the opening quote behind. Both forms are
    implemented literally as frozen; no vendored description uses the trailing
    form, so the inconsistency is unreachable today.
    ``test_trailing_terminator_form_is_unused_by_the_real_corpus`` pins that.

    ``strip('"')`` is never applied to the plain branch: 22 of the 50 plain
    descriptions contain a ``"`` character and would be corrupted.
    """
    value = raw.strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            # Manual unescape of the two escapes the corpus can contain, then
            # drop the outer quotes. Deliberately not a general YAML reader.
            inner = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            return inner.rstrip()
        if isinstance(decoded, str):
            return decoded
        raise SkillParseError(
            "FRONTMATTER_UNPARSEABLE", "description is not a string scalar"
        )

    marker = '": "'
    if marker in value:
        return value.split(marker, 1)[1].rstrip()
    if value.endswith('":"'):
        return value[:-2]
    return value


def describe_quoting(text: str) -> str:
    """Return ``"double-quoted"`` or ``"plain"`` for the document's description.

    Exposed so a test can *measure* the corpus split instead of asserting a
    remembered number.
    """
    lines, _ = split_frontmatter(text)
    for line in lines:
        match = _DESCRIPTION_LINE.match(line)
        if match:
            value = match.group(1).strip()
            if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
                return "double-quoted"
            return "plain"
    raise SkillParseError("DESCRIPTION_MISSING", "no description key in frontmatter")


# --------------------------------------------------------------------- #
# the stdlib reader                                                       #
# --------------------------------------------------------------------- #


def _stdlib_frontmatter(text: str) -> dict[str, Any]:
    """Read exactly the measured frontmatter shape, refusing everything else.

    Accepted: ``key: scalar`` at column 0, ``metadata:`` opening a one-level map
    whose only child is ``  version: scalar``, ``# comment`` lines and blank
    lines. Everything else raises, because a wrong answer here becomes a wrong
    playbook injected into a turn.
    """
    lines, _ = split_frontmatter(text)

    data: dict[str, Any] = {}
    in_metadata = False
    for raw_line in lines:
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()

        if indent == 0:
            if stripped.endswith(":"):
                key = stripped[:-1].strip()
                if key in data:
                    raise SkillParseError(
                        "FRONTMATTER_UNPARSEABLE", f"duplicate key {key!r}"
                    )
                if key == "metadata":
                    in_metadata = True
                    data[key] = {}
                else:
                    # `key:` with no value is only legal for the metadata map.
                    raise SkillParseError(
                        "FRONTMATTER_UNPARSEABLE", f"empty value for key {key!r}"
                    )
                continue
            if ":" not in stripped:
                raise SkillParseError(
                    "FRONTMATTER_UNPARSEABLE", f"line is not 'key: scalar': {stripped!r}"
                )
            if in_metadata:
                raise SkillParseError(
                    "FRONTMATTER_UNPARSEABLE", "metadata map must be the last block"
                )
            key, _, value = stripped.partition(":")
            key = key.strip()
            if key in data:
                raise SkillParseError(
                    "FRONTMATTER_UNPARSEABLE", f"duplicate key {key!r}"
                )
            data[key] = _stdlib_scalar(raw_line, key)
            continue

        # indented
        if not in_metadata:
            raise SkillParseError(
                "FRONTMATTER_UNPARSEABLE", f"unexpected indentation: {stripped!r}"
            )
        if indent != 2:
            raise SkillParseError(
                "FRONTMATTER_UNPARSEABLE", f"nesting deeper than one level: {stripped!r}"
            )
        match = _VERSION_LINE.match(raw_line)
        if not match:
            raise SkillParseError(
                "FRONTMATTER_UNPARSEABLE", f"unsupported metadata child: {stripped!r}"
            )
        if "version" in data["metadata"]:
            raise SkillParseError(
                "FRONTMATTER_UNPARSEABLE", "duplicate metadata.version"
            )
        data["metadata"]["version"] = match.group(1).strip().strip('"').strip("'")

    if "metadata" in data and not data["metadata"]:
        raise SkillParseError("FRONTMATTER_UNPARSEABLE", "metadata map is empty")
    return data


def _stdlib_scalar(raw_line: str, key: str) -> str:
    """Extract one top-level scalar, applying the per-key quoting rule."""
    if key == "description":
        return unquote_description(_DESCRIPTION_LINE.match(raw_line).group(1))
    if key == "name":
        return _NAME_LINE.match(raw_line).group(1).strip().strip('"').strip("'")
    # Any other top-level scalar. Quoted or bare; there are none in the corpus
    # beyond the two above, so this stays a plain read rather than a guess.
    _, _, value = raw_line.partition(":")
    value = value.strip()
    if value.startswith(("&", "*", "[", "{", "|", ">")):
        raise SkillParseError(
            "FRONTMATTER_UNPARSEABLE", f"unsupported YAML construct for {key!r}"
        )
    return value.strip('"').strip("'")


# --------------------------------------------------------------------- #
# public entry point                                                      #
# --------------------------------------------------------------------- #


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return ``(frontmatter, parser_name)``; ``parser_name`` in :data:`PARSER_NAMES`.

    PyYAML is preferred, over the delimited body -- see
    :func:`parse_frontmatter_pyyaml` for why the body and not the whole document.
    The stdlib reader runs when PyYAML is not importable, and also when PyYAML
    declines the document -- so a construct the fallback refuses shows up as a
    parser disagreement rather than as a silent divergence.
    """
    pyyaml = parse_frontmatter_pyyaml(text)
    if pyyaml is not None:
        return pyyaml, "pyyaml"
    return _stdlib_frontmatter(text), "stdlib"


def parse_frontmatter_pyyaml(text: str) -> dict[str, Any] | None:
    """Raw PyYAML result, or ``None`` when PyYAML is absent or declines.

    PyYAML is handed the **delimited body** -- the lines *between* the ``---``
    markers -- not the whole document. That detail is load-bearing and was got
    wrong in plan §1.2.5, which passes the full text to ``yaml.safe_load``. In
    YAML ``---`` is a *document separator*, not a block fence, so
    ``safe_load("---\\nname: x\\n---\\n\\n# body\\n")`` raises
    ``ComposerError: expected a single document in the stream``. Measured over the
    vendored library: **0 of 50** files parse when handed the full document, and
    50 of 50 parse when handed the body. Passing the full text therefore makes
    ``parse_frontmatter`` fall through to the stdlib reader on *every* skill --
    a PyYAML-first parser that never uses PyYAML, silently.

    Deliberately does **not** fall back, so an equivalence test can tell the two
    parsers apart. A silent fallback here would make the equivalence assertion
    vacuous -- it would be comparing the stdlib reader with itself.
    """
    try:
        import yaml  # type: ignore
    except ImportError:
        return None
    try:
        lines, _ = split_frontmatter(text)
        loaded = yaml.safe_load("\n".join(lines))
    except Exception:
        return None
    return loaded if isinstance(loaded, dict) else None


def parse_frontmatter_both(text: str) -> dict[str, dict[str, Any] | None]:
    """Run both parsers and return ``{"pyyaml": ..., "stdlib": ...}``.

    Exists so the equivalence test can compare the two results directly instead
    of re-implementing the choice of parser at the call site. ``None`` for
    ``pyyaml`` means PyYAML is unavailable or refused the document; a test must
    treat that as unavailable, never as agreement.
    """
    return {
        "pyyaml": parse_frontmatter_pyyaml(text),
        "stdlib": _stdlib_frontmatter(text),
    }


# --------------------------------------------------------------------- #
# record fields                                                           #
# --------------------------------------------------------------------- #


def parse_name(frontmatter: dict[str, Any]) -> str:
    """The bare slug from frontmatter ``name``."""
    value = frontmatter.get("name")
    if not isinstance(value, str) or not value.strip():
        raise SkillParseError("FRONTMATTER_UNPARSEABLE", "name missing or empty")
    return value.strip().strip('"').strip("'")


def parse_version(frontmatter: dict[str, Any]) -> str:
    """``frontmatter.metadata.version``, validated as semver.

    Raises ``VERSION_MISSING`` when the key is absent and ``VERSION_MALFORMED``
    when it is present but not ``MAJOR.MINOR.PATCH``. A playbook with an
    unversioned definition cannot be reasoned about, so it is refused.
    """
    metadata = frontmatter.get("metadata")
    raw = metadata.get("version") if isinstance(metadata, dict) else None
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise SkillParseError("VERSION_MISSING", "frontmatter.metadata.version absent")
    version = str(raw).strip().strip('"').strip("'")
    if not _SEMVER.match(version):
        raise SkillParseError("VERSION_MALFORMED", version)
    return version


def parse_description(
    frontmatter: dict[str, Any], *, max_chars: int = DESCRIPTION_MAX_CHARS
) -> tuple[str, bool]:
    """Return ``(description, truncated)`` with whitespace collapsed."""
    raw = frontmatter.get("description")
    if not isinstance(raw, str) or not raw.strip():
        raise SkillParseError("DESCRIPTION_MISSING", "frontmatter.description absent")
    text = _WHITESPACE.sub(" ", raw.strip())
    if len(text) > max_chars:
        return text[:max_chars].rstrip(), True
    return text, False


def related_skills_from_section(text: str) -> tuple[str, ...]:
    """Slugs from the ``## Related Skills`` section, union of all three shapes.

    Self is excluded. The result is sorted so the field is stable across runs.
    """
    match = _RELATED_HEADING.search(text)
    if not match:
        return ()
    start = match.end()
    nxt = _NEXT_H2.search(text, start)
    section = text[start : nxt.start()] if nxt else text[start:]

    slugs: set[str] = set()
    for pattern in _RELATED_PATTERNS:
        slugs.update(pattern.findall(section))
    return tuple(sorted(slugs))


def related_skills_from_description(description: str) -> tuple[str, ...]:
    """Slugs from inline ``see <slug>`` cross-references in the description."""
    return tuple(sorted(set(_TAIL_REF.findall(description))))


def partition_related(
    candidates: tuple[str, ...], *, self_id: str, registered: set[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split referenced slugs into ``(resolved, unresolved)``, sorted, self out.

    Unresolved references are carried, never dropped: a broken cross-reference is
    a real defect in the library and hiding it would make routing look better
    than it is.
    """
    resolved = sorted(
        {s for s in candidates if s != self_id and s in registered}
    )
    unresolved = sorted(
        {s for s in candidates if s != self_id and s not in registered}
    )
    return tuple(resolved), tuple(unresolved)


# --------------------------------------------------------------------- #
# triggers -- mined from upstream, never hand-written                      #
# --------------------------------------------------------------------- #


def read_evals(path) -> tuple[tuple[str, ...], int, str | None]:
    """Return ``(triggers, eval_count, warning)`` from an ``evals.json``.

    Measured upstream schema (50/50 files)::

        {"skill_name": str, "evals": [{"id": int, "prompt": str, ...}]}

    A missing or malformed file is **not** a parse failure. The registry's job is
    to load playbooks; a missing eval corpus is an upstream packaging defect, and
    the honest handling is empty triggers plus a reported warning. Inventing
    trigger strings to fill the gap is forbidden.
    """
    from pathlib import Path

    evals_path = Path(path)
    if not evals_path.is_file():
        return (), 0, f"{evals_path.name}: absent"

    try:
        document = json.loads(evals_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return (), 0, f"{evals_path.name}: unreadable ({type(exc).__name__})"

    if not isinstance(document, dict):
        return (), 0, f"{evals_path.name}: top level is not an object"

    entries = document.get("evals")
    if not isinstance(entries, list):
        return (), 0, f"{evals_path.name}: no evals array"

    prompts: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        prompt = entry.get("prompt")
        if isinstance(prompt, str) and prompt.strip():
            prompts.append(prompt.strip())

    triggers = tuple(dict.fromkeys(p[:TRIGGER_MAX_CHARS] for p in prompts))
    return triggers, len(entries), None


def iter_triggers(triggers: tuple[str, ...]) -> Iterator[str]:
    """Convenience iterator (kept explicit so callers do not assume a list)."""
    for trigger in triggers:
        yield trigger


def is_valid_skill_id(skill_id: str) -> bool:
    """True for a directory name usable as a skill id: ``[a-z0-9-]+``."""
    return bool(_SKILL_ID.match(skill_id))
