"""DEV-008-SKILLS-OPS W3 — execution event contract catalog.

Owns three proofs the plan froze:

1. §1.3.1 — the wire catalog. 26 pre-existing names survive, exactly 11 new
   names were added, `turn_summary` is still absent, and the mission's other
   four (tool_completed / tool_failed / approval_required /
   synthesis_started) were already members and are only a client-side gap.
2. §1.3.2 / §1.3.5 — every frozen tree metadata key survives
   `sanitize_metadata` unchanged, and a poisoned metadata dict loses every
   forbidden key at every depth (top level, nested dicts, list items).
3. §1.3.3 / §3.4 — the new graph nodes are mapped to wire types and the new
   `LangGraphState` fields carry the reducer kind the fan-out needs.

W4 owns the DB leg of the poison test (`repos.ExecutionEvents.insert`), and
W7 owns the client listener array. This file is the pure/model/wire layer.
"""
import json
import operator
import re
import typing
from pathlib import Path

import pytest

from app.contracts import events as ev
from app.contracts.events import (
    LEGACY_EVENT_TYPES,
    TREE_STATUSES,
    GraphExecutionEvent,
    sanitize_metadata,
    sanitize_user_text,
)
from app.graphs.events import NODE_EVENT_MAP, NODE_LABELS, build_event
from app.graphs.state import (
    GRAPH_STATE_FIELDS,
    LIST_FIELDS,
    LangGraphState,
    initial_state,
    merge_lists,
)

# ---------- catalogs, as data (never a bare literal in product logic) ----------

# The 26 names that existed before DEV-008-SKILLS-OPS. A removal is a contract
# break, so the test pins the whole pre-run set, not just a sample.
PRE_RUN_EVENT_TYPES = (
    "turn_started", "provider_selected", "context_started",
    "context_completed", "rag_started", "rag_completed",
    "web_started", "web_completed", "web_source",
    "instagram_provider_started", "instagram_provider_completed",
    "delegation_started", "state_read", "tool_completed",
    "tool_failed", "job_created", "approval_required",
    "synthesis_started", "assistant_delta", "assistant_completed",
    "turn_completed", "turn_failed", "turn_closed", "stream_end",
    "model_delta", "model_completed",
)

# The 11 names §1.3.1 marks NEW.
NEW_EVENT_TYPES = (
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

# The 4 of the mission's 15 that were already members — W7's gap fix, not W3's.
PRE_EXISTING_MISSION_TYPES = (
    "tool_completed", "tool_failed",
    "approval_required", "synthesis_started",
)

MISSION_EVENT_TYPES = NEW_EVENT_TYPES + PRE_EXISTING_MISSION_TYPES

# §1.3.2, frozen label / detail / metadata table. Only the metadata key set is
# asserted here; labels and details are W4's to produce.
TREE_EVENT_METADATA_KEYS = {
    "account_manager_started": (
        "status", "project_id", "turn_id", "thread_id", "route", "parent_id",
    ),
    "task_planned": (
        "status", "task_count", "employee_count", "turn_id", "thread_id",
        "parent_id",
    ),
    "employee_queued": (
        "status", "employee_id", "employee_role", "turn_id", "thread_id",
        "parent_id",
    ),
    "employee_started": (
        "status", "employee_id", "employee_role", "turn_id", "thread_id",
        "parent_id",
    ),
    "employee_completed": (
        "status", "employee_id", "employee_role", "duration_ms", "completed_at",
        "parent_id",
    ),
    "employee_failed": (
        "status", "employee_id", "employee_role", "duration_ms", "completed_at",
        "parent_id",
    ),
    "skill_selected": (
        "status", "skill_id", "skill_name", "reason_code", "score",
        "employee_id", "parent_id",
    ),
    "skill_loaded": (
        "status", "skill_id", "skill_name", "skill_version", "category",
        "employee_id", "parent_id",
    ),
    "tool_started": (
        "status", "tool_id", "tool_run_id", "employee_id", "parent_id",
    ),
    "tool_completed": (
        "status", "tool_id", "tool_run_id", "duration_ms", "employee_id",
        "parent_id",
    ),
    "tool_failed": (
        "status", "tool_id", "tool_run_id", "employee_id", "parent_id",
    ),
    "evidence_added": (
        "status", "evidence_count", "evidence_kind", "employee_id", "parent_id",
    ),
    "synthesis_started": ("status", "parent_id"),
    "synthesis_completed": ("status", "duration_ms", "parent_id"),
    "approval_required": ("status", "approval_id", "action_class", "parent_id"),
}

# §1.3.6: tree metadata is flat, <= 12 keys per event, detail <= 160 chars.
MAX_TREE_META_KEYS = 12
MAX_TREE_DETAIL_CHARS = 160
METADATA_JSON_TRUNCATION = 4000

# The union of every frozen key. §1.3.2 requires each to survive sanitisation
# for all 37 event types, not only the 15 tree ones.
ALL_FROZEN_META_KEYS = tuple(sorted({
    key for keys in TREE_EVENT_METADATA_KEYS.values() for key in keys
}))

# The nodes W4 introduces (§3.4 W3). Each must have an explicit wire type so
# graph_runtime._emit stops falling through build_event's generic default.
NEW_GRAPH_NODES = (
    "skill_route", "skill_load", "employee_fanout", "employee",
    "employee_fanin", "evidence_collect", "synthesis_complete",
)

# §3.4 W3: the 6 new LangGraphState fields, with the reducer kind each one
# needs. 4 accumulate across Send branches, 2 are turn-level scalars.
NEW_LIST_STATE_FIELDS = (
    "skill_ids", "skill_reason_codes", "employee_roles", "evidence",
)
NEW_SCALAR_STATE_FIELDS = (
    "skill_selection_reason", "foundation_injected",
)
NEW_STATE_FIELDS = NEW_LIST_STATE_FIELDS + NEW_SCALAR_STATE_FIELDS

# The pre-existing 20 exact-match entries, verbatim (§1.3.5: preserved so no
# existing sanitisation test can weaken).
PRE_RUN_FORBIDDEN_META_KEYS = frozenset({
    "prompt", "system", "secret", "api_key", "token", "access_token",
    "refresh_token", "authorization", "password", "credential", "traceback",
    "chain_of_thought", "reasoning_content", "exception", "stderr", "stdout",
    "raw", "debug", "env", "environment", "provider_url", "base_url",
})

ADDED_FORBIDDEN_META_KEYS = frozenset({
    "reasoning", "reasoning_trace", "thoughts", "thinking", "scratchpad",
    "deliberation", "monologue", "internal_notes", "plan_of_thought",
    "cot", "rationale",
})

# Keys the exact-match-only filter used to let through. D7 was that
# `reasoning_trace`, `thoughts` and `scratchpad` all passed; this is the
# regression list.
PREFIX_ONLY_FORBIDDEN_KEYS = (
    "reasoning_steps", "thought_chain", "thinking_notes", "scratchpad_text",
    "deliberation_log", "monologue_run", "internal_note_draft", "cot_chain",
    "raw_model_output",
)
SUFFIX_ONLY_FORBIDDEN_KEYS = (
    "system_prompt", "user_prompt", "llm_system", "bundle_raw", "vault_secret",
    "graph_token", "acme_api_key", "db_password", "service_credential",
)
ENV_SYMBOL_FORBIDDEN_KEYS = (
    "AI_RUNTIME", "OMOS_DATA_DIR", "MAX_AGENT_CONCURRENCY", "LLM_PROVIDER",
)
FORBIDDEN_VALUE_SUBSTRINGS = (
    "sk-", "Bearer ", "Traceback", "chain of thought", "you are a helpful",
    "AI_RUNTIME", "sk-xyz", ".py", "secret\\app",
)


# ---------- QA MEDIUM-1 remediation: the ai-seo skill-id cutout ----------

# A fake AI_-prefixed product-env-shaped value that is NOT a pinned skill id:
# the general env-symbol redaction must keep catching it.
FAKE_AI_ENV_VALUE = "AI_TELEMETRY_URL"

# The mixture that must be partly intact and partly redacted after the fix.
CUTOUT_VALUE_PROBE = (
    ("Using ai-seo", "Using ai-seo"),          # the false positive, now fixed
    ("Using seo-audit", "Using seo-audit"),    # was never redacted
    ("AI_RUNTIME", "[REDACTED]"),              # exact _ENV_SYMBOLS member
    ("AI_RUNTIME_S", "[REDACTED]"),            # AI_-prefixed product env name
    ("OPENAI_API_KEY", "[REDACTED]"),          # other _ENV_SYMBOLS prefix
    ("AI_TELEMETRY_URL", "[REDACTED]"),        # fake AI_ env value stays red
)

# Every secret/protection class the cutout must NOT weaken.
STILL_FORBIDDEN_TEXT_VALUES = (
    "OPENAI_API_KEY", "OPENAI_API_KEY=sk-abc1234567890",
    "AI_RUNTIME=langgraph", "Bearer abc.def.ghi", "sk-abcdef0123456789",
    "Traceback (most recent call last):", "vault://prod/db",
    "-----BEGIN RSA PRIVATE KEY-----",
)

# Things that must still redact because they are env-symbol shaped but NOT
# pinned skill ids — the cutout's three AND conditions must not be bypassable
# by near-miss spellings.
CUTOUT_NEAR_MISS_VALUES = (
    "AI_TELEMETRY_URL",   # AI_-prefixed but not in the allowlist
    "ai-seo-x",           # allowlisted prefix but a different id
    "AI_SEO_SUFFIX",      # underscore form, not allowlisted
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _library_skill_ids() -> tuple[str, ...]:
    """The real pinned library, discovered on disk — never typed in."""
    library = _repo_root() / ".agents" / "skills"
    return tuple(sorted(
        path.name for path in library.iterdir()
        if path.is_dir() and re.fullmatch(r"[a-z0-9][a-z0-9-]*", path.name)
    ))


# ---------- QA MEDIUM-1: the ai-seo redaction false positive (fixed) ----------

@pytest.mark.parametrize("value,expected", CUTOUT_VALUE_PROBE)
def test_skill_id_survives_while_secret_classes_still_redact(value, expected):
    """`Using ai-seo` survives `sanitize_user_text`; every other protection
    class in the same mixture redacts exactly as before the fix."""
    assert sanitize_user_text(value, 160) == expected
    if expected == "[REDACTED]":
        assert "[REDACTED]" not in sanitize_user_text(
            "Using seo-audit", 160)


def test_ai_prefixed_skill_id_allowlist_is_static_and_versioned():
    """The cutout must be a small explicit tuple, not a registry import."""
    assert isinstance(ev.AI_PREFIXED_SKILL_IDS, tuple)
    assert ev.AI_PREFIXED_SKILL_IDS == ("ai-seo",)
    for member in ev.AI_PREFIXED_SKILL_IDS:
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", member), member
        # The reason the filter fired at all: canonical form carries the AI_
        # prefix. A allowlist without that constraint could whitewash anything.
        assert re.sub(r"[^a-z0-9]+", "_", member).upper().startswith("AI_")


def test_every_non_allowlisted_ai_prefixed_value_still_redacts():
    for value in CUTOUT_NEAR_MISS_VALUES:
        assert sanitize_user_text(value, 160) == "[REDACTED]", value
    # The cutout is membership-guarded, not spelling-guarded: canonically the
    # SAME id (hyphens/underscores/leading "AI_SEO" casing) stays visible,
    # while everything else that scans as an env symbol keeps redacting.
    assert sanitize_user_text("AI_SEO_SUFFIX", 160) == "[REDACTED]"
    assert sanitize_user_text("Using AI_SEO_SUFFIX", 160) == "Using [REDACTED]"


def test_secret_and_chain_of_thought_classes_survive_the_cutout():
    for value in STILL_FORBIDDEN_TEXT_VALUES:
        assert value not in sanitize_user_text(value, 6000), value
    for label in ("reasoning", "thoughts", "scratchpad", "internal_notes"):
        assert ev._forbidden_meta_key(label)
        assert label not in sanitize_metadata({label: "x"})


def test_env_symbol_predicate_agrees_with_the_wire_filter():
    """`_is_env_symbol` is shared by label, detail and metadata paths; the
    cutout sits inside it, so the predicate itself is pinned."""
    assert ev._is_env_symbol("AI_RUNTIME") is True
    assert ev._is_env_symbol(FAKE_AI_ENV_VALUE) is True
    assert ev._is_env_symbol("AI_SEO_SUFFIX") is True
    assert ev._is_env_symbol("ai_seo") is False
    assert ev._is_env_symbol("ai-seo") is False
    assert ev._is_env_symbol("Ompp_data_dir-not-real") is False
    assert ev._is_env_symbol("OMOS_DATA_DIR") is True


def test_allowlist_derives_from_the_pinned_library_not_hardcoded_membership():
    """Derive membership from the real library on disk.

    The allowlist is static in `events.py` (the contract must not import the
    registry), so this test is the drift alarm: if a future re-pin adds
    another `ai-...`-prefixed playbook id, the canonicalises-to-AI_ set changes
    and this test fails with instructions instead of shipping `[REDACTED]`
    silently again.
    """
    ids = _library_skill_ids()
    assert len(ids) == 50, (
        f"the pinned library size changed: {len(ids)}; re-measure the "
        "AI_-prefixed distribution and update AI_PREFIXED_SKILL_IDS if a "
        "second ai-...-prefixed playbook was added"
    )
    ai_prefixed = sorted(
        sid for sid in ids
        if re.sub(r"[^a-z0-9]+", "_", sid).upper().startswith("AI_")
    )
    assert ai_prefixed == ["ai-seo"], ai_prefixed
    assert tuple(ai_prefixed) == ev.AI_PREFIXED_SKILL_IDS
    # and the fixed wire behaviour on the real library ids:
    for sid in ids:
        assert sanitize_user_text(f"Using {sid}", 160) == f"Using {sid}", sid


def test_registry_derives_the_same_ai_prefix_distribution():
    """Cross-check the disk listing against the loaded registry (the router
    set), in case a manifest ever ships empty directories."""
    from app.services.skills.registry import load_registry
    reg = load_registry(disabled="")
    ids = [r.skill_id for r in reg.enabled_records()]
    assert len(ids) == 50
    assert set(_library_skill_ids()) == set(ids), (
        "library on disk and registry disagree")
    ai_prefixed = sorted(
        sid for sid in ids
        if re.sub(r"[^a-z0-9]+", "_", sid).upper().startswith("AI_")
    )
    assert ai_prefixed == list(ev.AI_PREFIXED_SKILL_IDS)


def test_cutout_comment_names_the_measured_distribution():
    """The rationale must stay in the file — grep it rather than importing
    behaviour into the test."""
    source = (_repo_root() / "app" / "contracts" / "events.py").read_text(
        encoding="utf-8")
    assert "AI_PREFIXED_SKILL_IDS" in source
    assert "from app.services" not in source, (
        "events.py must not depend on the product skills package")


# ---------- §1.3.1 the catalog ----------

def test_catalog_size_is_derived_not_hardcoded():
    expected = len(PRE_RUN_EVENT_TYPES) + len(NEW_EVENT_TYPES)
    assert len(LEGACY_EVENT_TYPES) == expected, (
        "wire catalog drifted from the frozen contract: "
        f"expected {expected} ({len(PRE_RUN_EVENT_TYPES)} pre-run + "
        f"{len(NEW_EVENT_TYPES)} new), found {len(LEGACY_EVENT_TYPES)}"
    )
    # The plan's stated end state, stated out loud so a reviewer reads the
    # number rather than having to recompute it.
    assert expected == 37
    assert len(PRE_RUN_EVENT_TYPES) == 26


def test_every_pre_run_event_type_survives():
    missing = [t for t in PRE_RUN_EVENT_TYPES if t not in LEGACY_EVENT_TYPES]
    assert missing == [], f"contract break: removed {missing}"


@pytest.mark.parametrize("event_type", NEW_EVENT_TYPES)
def test_new_event_type_is_registered(event_type):
    assert event_type in LEGACY_EVENT_TYPES


@pytest.mark.parametrize("event_type", PRE_EXISTING_MISSION_TYPES)
def test_preexisting_mission_type_is_still_registered(event_type):
    """§1.3.1 rows 12-15: already members, W7's gap fix, not W3's add."""
    assert event_type in LEGACY_EVENT_TYPES


def test_all_fifteen_mission_types_are_members():
    assert set(MISSION_EVENT_TYPES) <= set(LEGACY_EVENT_TYPES)
    assert len(MISSION_EVENT_TYPES) == 15
    assert len(set(MISSION_EVENT_TYPES)) == 15


def test_catalog_has_no_duplicates():
    assert len(set(LEGACY_EVENT_TYPES)) == len(LEGACY_EVENT_TYPES)


def test_turn_summary_stays_out_of_band():
    """§1.3.1 / §8.3: turn_summary is dropped by the chat.py filter."""
    assert "turn_summary" not in LEGACY_EVENT_TYPES
    with pytest.raises(ValueError):
        GraphExecutionEvent(project_id="starter", event_type="turn_summary")


def test_unknown_event_type_is_still_rejected():
    with pytest.raises(ValueError):
        GraphExecutionEvent(project_id="starter", event_type="not_a_real_type")


# ---------- §1.3.4 the visual status enum ----------

def test_tree_statuses_is_the_frozen_seven():
    assert len(TREE_STATUSES) == 7
    assert len(set(TREE_STATUSES)) == 7
    assert TREE_STATUSES == (
        "QUEUED", "RUNNING", "COMPLETE", "FAILED",
        "RETRYING", "WAITING_FOR_APPROVAL", "CANCELLED",
    )


def test_every_status_the_frozen_table_produces_is_in_the_enum():
    produced = {"QUEUED", "RUNNING", "COMPLETE", "FAILED", "RETRYING",
                "WAITING_FOR_APPROVAL"}
    assert produced <= set(TREE_STATUSES)
    assert set(TREE_STATUSES) - produced == {"CANCELLED"}, (
        "CANCELLED is declared but never produced end-to-end in this run "
        "(no cancel endpoint) — §1.3.4 discloses this"
    )


# ---------- §1.3.2 every frozen metadata key survives sanitisation ----------

def test_frozen_metadata_table_covers_exactly_the_fifteen_mission_types():
    assert set(TREE_EVENT_METADATA_KEYS) == set(MISSION_EVENT_TYPES)


@pytest.mark.parametrize("key", ALL_FROZEN_META_KEYS)
def test_frozen_metadata_key_survives_sanitize_metadata(key):
    """§1.3.2: none of the frozen keys is exact/prefix/suffix/env-matched."""
    assert key in sanitize_metadata({key: "inert-value"}), (
        f"{key!r} is silently dropped by sanitize_metadata"
    )


@pytest.mark.parametrize("event_type", LEGACY_EVENT_TYPES)
def test_all_frozen_keys_survive_for_every_event_type(event_type):
    """§1.3.2: asserted for all 37 types x all keys, not just the tree 15."""
    assert event_type in LEGACY_EVENT_TYPES
    payload = {key: "inert-value" for key in ALL_FROZEN_META_KEYS}
    assert set(sanitize_metadata(payload)) == set(ALL_FROZEN_META_KEYS)


def test_frozen_metadata_keys_are_all_json_safe_values():
    """The keys must round-trip through metadata_json, so values are scalar."""
    for keys in TREE_EVENT_METADATA_KEYS.values():
        assert len(keys) <= MAX_TREE_META_KEYS, keys
        payload = {k: "x" for k in keys}
        assert len(json.dumps(payload, ensure_ascii=False)) < \
            METADATA_JSON_TRUNCATION


def test_maximum_shape_event_round_trips_through_to_row_and_from_row():
    """§1.3.6 bound, on the pure path. W4 owns the DB leg."""
    for event_type, keys in TREE_EVENT_METADATA_KEYS.items():
        metadata = {k: "x" * 160 for k in keys}
        row = GraphExecutionEvent(
            project_id="starter", turn_id="t-1", event_type=event_type,
            label="l" * 300, detail="d" * MAX_TREE_DETAIL_CHARS,
            metadata=metadata,
        ).to_row()
        assert len(row["metadata_json"]) < METADATA_JSON_TRUNCATION, event_type
        back = GraphExecutionEvent.from_row({**row, "id": 1})
        assert back.event_type == event_type
        assert back.metadata == metadata, event_type


# ---------- §1.3.5 the chain-of-thought filter ----------

def test_pre_run_forbidden_keys_are_preserved_verbatim():
    assert PRE_RUN_FORBIDDEN_META_KEYS <= ev._FORBIDDEN_META_KEYS
    assert ADDED_FORBIDDEN_META_KEYS <= ev._FORBIDDEN_META_KEYS
    assert not (ADDED_FORBIDDEN_META_KEYS & PRE_RUN_FORBIDDEN_META_KEYS)


def test_forbidden_prefix_and_suffix_sets_are_the_frozen_ones():
    assert ev._FORBIDDEN_META_PREFIXES == (
        "reasoning", "thought", "thinking", "scratchpad",
        "deliberat", "monologue", "internal_note", "cot_", "raw_",
    )
    assert ev._FORBIDDEN_META_SUFFIXES == (
        "_prompt", "_system", "_raw", "_secret", "_token",
        "_api_key", "_password", "_credential",
    )


@pytest.mark.parametrize("key", PREFIX_ONLY_FORBIDDEN_KEYS)
def test_prefix_rule_catches_what_exact_match_missed(key):
    assert key not in ev._FORBIDDEN_META_KEYS, "fixture must be prefix-only"
    assert ev._forbidden_meta_key(key)
    assert key not in sanitize_metadata({key: "x"})


@pytest.mark.parametrize("key", SUFFIX_ONLY_FORBIDDEN_KEYS)
def test_suffix_rule_catches_what_exact_match_missed(key):
    assert key not in ev._FORBIDDEN_META_KEYS, "fixture must be suffix-only"
    assert ev._forbidden_meta_key(key)
    assert key not in sanitize_metadata({key: "x"})


@pytest.mark.parametrize("key", ENV_SYMBOL_FORBIDDEN_KEYS)
def test_env_symbol_rule_still_catches(key):
    assert ev._forbidden_meta_key(key)
    assert key not in sanitize_metadata({key: "x"})


@pytest.mark.parametrize("key", ALL_FROZEN_META_KEYS)
def test_no_frozen_key_is_collaterally_forbidden(key):
    assert not ev._forbidden_meta_key(key), key


def test_filter_normalises_case_and_separators():
    for variant in ("Reasoning", "REASONING_TRACE", "reasoning-trace",
                    "  reasoning.trace  ", "cot chain"):
        assert ev._forbidden_meta_key(variant), variant


def test_empty_and_none_keys_are_not_forbidden():
    # A falsy key normalises to "" — not forbidden, so the pair survives.
    # Asserted so the "" branch is a documented decision, not an accident.
    assert not ev._forbidden_meta_key("")
    assert not ev._forbidden_meta_key(None)
    assert sanitize_metadata({"": "x"}) == {"": "x"}


# ---------- the poisoned-metadata fixture (§1.3.5 / §6.3 proof 1) ----------

# Exactly 12 forbidden top-level keys, chosen to span all three new rules plus
# two pre-existing exact entries, so the fixture proves both directions.
POISONED_TOP_LEVEL_KEYS = (
    "prompt", "traceback",
    "reasoning", "reasoning_trace", "thoughts", "scratchpad",
    "reasoning_steps", "thought_chain", "cot_chain", "raw_model_output",
    "system_prompt", "llm_system",
)
assert len(POISONED_TOP_LEVEL_KEYS) == 12

POISONED_SAFE_KEYS = (
    "status", "turn_id", "thread_id", "project_id", "route", "parent_id",
    "task_count", "employee_count", "employee_id", "employee_role",
    "duration_ms", "completed_at", "skill_id", "skill_name", "skill_version",
    "category", "reason_code", "score", "tool_id", "tool_run_id",
    "evidence_count", "evidence_kind", "approval_id", "action_class",
)


def _poisoned_metadata() -> dict:
    """12 forbidden top-level keys, 3 nested dicts, 2 lists of forbidden
    strings, one list of dicts, one BaseException value, and 24 safe keys."""
    meta: dict = {key: "poisoned" for key in POISONED_TOP_LEVEL_KEYS}
    meta.update({key: "safe" for key in POISONED_SAFE_KEYS})
    # 3 nested dicts, each carrying its own forbidden keys.
    meta["nested_a"] = {
        "status": "RUNNING",
        "reasoning": "chain of thought text",
        "prompt": "you are a helpful marketing assistant",
    }
    meta["nested_b"] = {
        "tool_id": "web_search",
        "tool_run_id": "tr-1",
        "system_prompt": "you are a helpful marketing assistant",
    }
    meta["nested_c"] = {
        "employee_id": "emp-abcdefgh-00",
        "raw_model_output": "chain of thought text",
        "cot_chain": "chain of thought text",
    }
    # 2 lists holding forbidden strings (secret values, env assignments).
    meta["poisoned_list_a"] = [
        "sk-abcdef0123456789",
        "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.SflKxwRJSMeKKF2QT4",
    ]
    meta["poisoned_list_b"] = [
        "AI_RUNTIME=langgraph",
        "LLM_API_KEY=sk-xyz0123456789",
    ]
    # 1 list of dicts: proves lists recurse into the same key filter.
    meta["poisoned_list_c"] = [
        {"status": "COMPLETE", "thoughts": "chain of thought text"},
        {"scratchpad": "chain of thought text", "parent_id": "9"},
    ]
    # 1 BaseException value carrying a traceback and an absolute path.
    meta["employee_error"] = RuntimeError(
        "Traceback (most recent call last):\n"
        '  File "C:\\secret\\app\\tools.py", line 12, in run\n'
        "RuntimeError: provider failed with sk-abcdef0123456789"
    )
    return meta


def _walk_keys(value):
    """Yield every mapping key at every depth of a sanitised structure."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk_keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_keys(item)


def _walk_strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_strings(item)
    elif isinstance(value, str):
        yield value


def test_poisoned_fixture_shape_is_what_the_plan_specified():
    meta = _poisoned_metadata()
    forbidden_present = [k for k in POISONED_TOP_LEVEL_KEYS if k in meta]
    assert len(forbidden_present) == 12
    nested = [k for k, v in meta.items() if isinstance(v, dict)]
    assert len(nested) == 3
    lists = [k for k, v in meta.items() if isinstance(v, list)]
    assert len(lists) == 3  # 2 forbidden-string lists + 1 list of dicts
    assert sum(1 for v in meta.values() if isinstance(v, BaseException)) == 1


def _pre_run_forbidden_meta_key(key: str) -> bool:
    """The pre-run filter, reproduced: exact-match plus env-symbol only.

    §1.3.5 / D7: exact-match alone let reasoning, reasoning_trace, thoughts,
    scratchpad, reasoning_steps, thought_chain, cot_chain, raw_model_output and
    system_prompt through. Re-implementing it here is what makes the
    "before/after" comparison measured rather than asserted.
    """
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", str(key or "").strip()).lower()
    return normalized in PRE_RUN_FORBIDDEN_META_KEYS or ev._is_env_symbol(
        normalized)


def test_poisoned_fixture_is_actually_dangerous_before_sanitising():
    """Guards the test: a fixture the old filter already cleaned proves nothing.

    The leak set is computed from the reproduced pre-run filter, not typed in,
    so it cannot drift away from the code it is describing.
    """
    fixture = _poisoned_metadata()
    leaked_before = sorted(
        key for key in POISONED_TOP_LEVEL_KEYS
        if not _pre_run_forbidden_meta_key(key)
    )
    assert leaked_before, "fixture must leak under the pre-run filter"
    for key in ("reasoning", "reasoning_trace", "thoughts", "scratchpad",
                "reasoning_steps", "thought_chain", "cot_chain",
                "raw_model_output", "system_prompt"):
        assert key in leaked_before, f"{key} did not leak before the fix"
    # ... and every one of them is caught now.
    assert not [k for k in leaked_before if not ev._forbidden_meta_key(k)]
    # The keys the pre-run filter already caught stay caught.
    for key in POISONED_TOP_LEVEL_KEYS:
        assert key in fixture


def test_poisoned_metadata_loses_every_forbidden_key_at_every_depth():
    clean = sanitize_metadata(_poisoned_metadata())
    leaked = sorted({
        key for key in _walk_keys(clean) if ev._forbidden_meta_key(key)
    })
    assert leaked == [], f"forbidden keys survived sanitisation: {leaked}"


def test_poisoned_metadata_keeps_every_safe_key():
    clean = sanitize_metadata(_poisoned_metadata())
    for key in POISONED_SAFE_KEYS:
        assert key in clean, f"{key} was dropped but is not forbidden"
    assert set(clean["nested_b"]) == {"tool_id", "tool_run_id"}
    assert set(clean["nested_c"]) == {"employee_id"}
    assert set(clean["nested_a"]) == {"status"}
    assert clean["poisoned_list_c"][1] == {"parent_id": "9"}


def test_poisoned_metadata_leaks_no_forbidden_value_substring():
    clean = sanitize_metadata(_poisoned_metadata())
    blob = json.dumps(clean, ensure_ascii=False, default=str)
    hits = [s for s in FORBIDDEN_VALUE_SUBSTRINGS if s in blob]
    assert hits == [], f"forbidden values survived sanitisation: {hits}"


def test_poisoned_exception_value_becomes_the_generic_message():
    clean = sanitize_metadata(_poisoned_metadata())
    assert clean["employee_error"] == ev._GENERIC_ERROR


def test_poisoned_metadata_is_json_serialisable_after_sanitising():
    clean = sanitize_metadata(_poisoned_metadata())
    assert json.loads(json.dumps(clean, ensure_ascii=False)) == clean


def test_poisoned_metadata_is_clean_on_the_model_and_wire_paths():
    """§1.3.5 paths 2 and 3: GraphExecutionEvent -> to_row -> from_row -> to_sse.

    Path 1 (the write path through turns._event_row) and the DB leg belong to
    W4; this covers the model and SSE/wire paths that also live in the file W3
    owns.
    """
    event = GraphExecutionEvent(
        project_id="starter", conversation_id="c1", turn_id="t-1",
        event_type="employee_failed", label="Research could not finish",
        metadata=_poisoned_metadata(),
    )
    row = event.to_row()
    blob = row["metadata_json"]
    for needle in FORBIDDEN_VALUE_SUBSTRINGS:
        assert needle not in blob, needle
    back = GraphExecutionEvent.from_row({**row, "id": 42})
    sse = back.to_sse(id=42)
    assert sse["event"] == "employee_failed"
    wire = json.dumps(sse["data"], ensure_ascii=False)
    for needle in FORBIDDEN_VALUE_SUBSTRINGS:
        assert needle not in wire, needle
    leaked = sorted({
        key for key in _walk_keys(sse["data"]["meta"])
        if ev._forbidden_meta_key(key)
    })
    assert leaked == [], leaked


# ---------- §1.3.3 / §3.4 the new graph nodes ----------

def test_every_node_maps_to_a_legal_wire_type():
    for node, event_type in NODE_EVENT_MAP.items():
        assert event_type in LEGACY_EVENT_TYPES, (node, event_type)


@pytest.mark.parametrize("node", NEW_GRAPH_NODES)
def test_new_node_is_mapped_to_an_explicit_wire_type(node):
    assert node in NODE_EVENT_MAP, f"{node} would fall through to tool_completed"
    assert NODE_EVENT_MAP[node] in LEGACY_EVENT_TYPES
    assert NODE_EVENT_MAP[node] != "tool_completed"
    assert NODE_LABELS.get(node), f"{node} has no default human label"


@pytest.mark.parametrize("node", NEW_GRAPH_NODES)
def test_new_node_builds_the_mapped_event(node):
    event = build_event(node, project_id="starter", turn_id="t-1",
                        conversation_id="c1")
    assert event.event_type == NODE_EVENT_MAP[node]
    assert event.label == NODE_LABELS[node]
    assert event.event_type in LEGACY_EVENT_TYPES


@pytest.mark.parametrize("node", NEW_GRAPH_NODES)
def test_new_node_survives_the_emit_path(node):
    """What graph_runtime._emit does for a node, without a database."""
    event = build_event(node, project_id="starter", turn_id="t-1",
                        conversation_id="c1", detail="d" * 300,
                        metadata={"status": "RUNNING", "parent_id": "7"})
    assert event.event_type in LEGACY_EVENT_TYPES
    assert event.metadata["status"] == "RUNNING"


def test_pre_existing_node_mappings_are_unchanged():
    frozen = {
        "load_project": "state_read",
        "load_conversation": "context_completed",
        "understand": "tool_completed",
        "state_only": "state_read",
        "knowledge": "rag_completed",
        "external_research": "web_completed",
        "social_research": "instagram_provider_completed",
        "deep_research": "delegation_started",
        "campaign_operation": "tool_completed",
        "approval_operation": "approval_required",
        "job_followup": "state_read",
        "aggregate": "tool_completed",
        "synthesize": "synthesis_started",
        "approval-interrupt": "approval_required",
        "respond": "turn_completed",
        "respond_failed": "turn_failed",
    }
    for node, event_type in frozen.items():
        assert NODE_EVENT_MAP.get(node) == event_type, node


# ---------- §1.3 / §3.4 the new LangGraphState fields ----------

def _annotated(field: str):
    """Return the Annotated metadata tuple for a reducer-annotated field."""
    annotation = LangGraphState.__annotations__[field]
    assert typing.get_origin(annotation) is typing.Annotated, field
    return typing.get_args(annotation)[1:]


@pytest.mark.parametrize("field", NEW_STATE_FIELDS)
def test_new_state_field_is_declared(field):
    assert field in LangGraphState.__annotations__
    assert field in GRAPH_STATE_FIELDS
    state = initial_state(project_id="starter", conversation_id="c",
                          turn_id="t", user_request="hi")
    assert field in state, f"{field} is missing from initial_state()"


@pytest.mark.parametrize("field", NEW_LIST_STATE_FIELDS)
def test_new_list_field_uses_the_append_reducer(field):
    assert _annotated(field) == (operator.add,), field
    assert field in LIST_FIELDS, field
    assert LangGraphState.__annotations__[field] == \
        LangGraphState.__annotations__["errors"], (
        "accumulator lists must use the same reducer as the existing ones"
    )


@pytest.mark.parametrize("field", NEW_SCALAR_STATE_FIELDS)
def test_new_scalar_field_overwrites(field):
    annotation = LangGraphState.__annotations__[field]
    assert typing.get_origin(annotation) is not typing.Annotated, field
    assert field not in LIST_FIELDS, field
    assert annotation is bool or annotation is str, field


def test_new_list_fields_start_empty_and_scalars_start_neutral():
    state = initial_state(project_id="starter", conversation_id="c",
                          turn_id="t", user_request="hi")
    for field in NEW_LIST_STATE_FIELDS:
        assert state[field] == [], field
    assert state["skill_selection_reason"] == ""
    assert state["foundation_injected"] is False


def test_pre_run_state_fields_are_untouched():
    frozen = (
        "project_id", "conversation_id", "turn_id", "user_request",
        "project_context", "state_results", "retrieved_evidence",
        "web_results", "social_results", "research_tasks", "worker_results",
        "approval_state", "model_route", "model_usage", "errors",
        "final_answer", "conversation_context", "project_state",
        "intent_type", "context_sources_used", "rag_invoked", "rag_query",
        "rag_hits", "rag_debug", "tool_capability_name",
    )
    for field in frozen:
        assert field in LangGraphState.__annotations__, field
        assert field in GRAPH_STATE_FIELDS, field


def test_parallel_fan_out_merges_the_new_list_fields():
    """The declared reducer kind has to actually merge under LangGraph."""
    from langgraph.graph import END, START, StateGraph

    def branch_a(_state):
        return {"skill_ids": ["seo-audit"], "employee_roles": ["seo"],
                "evidence": [{"kind": "web"}], "skill_reason_codes": [
                    "deterministic-exact-match"]}

    def branch_b(_state):
        return {"skill_ids": ["cro"], "employee_roles": ["cro"],
                "evidence": [{"kind": "internal"}], "skill_reason_codes": [
                    "related-skill-expansion"]}

    builder = StateGraph(LangGraphState)
    builder.add_node("a", branch_a)
    builder.add_node("b", branch_b)
    builder.add_edge(START, "a")
    builder.add_edge(START, "b")
    builder.add_edge("a", END)
    builder.add_edge("b", END)
    out = builder.compile().invoke(
        initial_state(project_id="starter", conversation_id="c",
                      turn_id="t", user_request="hi")
    )
    assert sorted(out["skill_ids"]) == ["cro", "seo-audit"]
    assert sorted(out["employee_roles"]) == ["cro", "seo"]
    assert len(out["evidence"]) == 2
    assert sorted(out["skill_reason_codes"]) == [
        "deterministic-exact-match", "related-skill-expansion",
    ]


def test_sequential_router_writes_the_new_scalars():
    """Scalars overwrite, so the router's decision is the value that survives."""
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(LangGraphState)
    builder.add_node("route", lambda _s: {
        "skill_selection_reason": "deterministic-keyword-match",
        "foundation_injected": True,
    })
    builder.add_node("fanout", lambda _s: {
        "skill_selection_reason": "not-overwritten-by-a-branch",
        "foundation_injected": False,
    })
    builder.add_edge(START, "route")
    builder.add_edge("route", "fanout")
    builder.add_edge("fanout", END)
    out = builder.compile().invoke(
        initial_state(project_id="starter", conversation_id="c",
                      turn_id="t", user_request="hi")
    )
    assert out["skill_selection_reason"] == "not-overwritten-by-a-branch"
    assert out["foundation_injected"] is False


def test_merge_lists_still_works_for_the_new_accumulators():
    assert merge_lists(["a"], ["b"]) == ["a", "b"]
    assert merge_lists([], []) == []
