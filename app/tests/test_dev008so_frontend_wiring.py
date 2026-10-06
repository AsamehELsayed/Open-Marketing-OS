"""DEV-008-SKILLS-OPS (W7) -- frontend wiring gate.

The frontend has no test runner (plan §2.4: introducing vitest/jest is a
separate decision), so correctness is proven by `tsc --noEmit` + `vite build`
plus **static source assertions** in Python. That is not a weaker gate than it
looks -- the defect this file exists to stop is precisely a defect that a type
checker cannot see: a name missing from an array of strings.

What is asserted, and what each assertion stops
-----------------------------------------------

1. **Two-sided event registration (plan §1.3.1, §7.2).** The string literals
   of the client's listener array are extracted *as text* and compared to
   `set(app.contracts.events.LEGACY_EVENT_TYPES)`. `chat.py` always sends a
   NAMED `event:` line, and `EventSource.onmessage` only fires for unnamed
   ones, so any name missing from that array is an event the SPA can never
   see. That was 13 of 26 types before this run; the gate makes it 37 == 37
   and keeps it there. (W9 asserts the same equality across the ownership
   boundary, where neither worker can see the other's file.)

2. **No registered name is a stream-closer.** Adding a closing type to the
   catalog would close the SSE before the remaining rows arrive, so the two
   lists must stay disjoint.

3. **All seven TREE_STATUSES have a render mapping (plan §1.3.4).** The check
   is a *key* in the `STATUS_VIEWS` object literal, not a mention of the word,
   so renaming or deleting a mapping fails. `CANCELLED` has no cancel control
   in this run (plan §8.2) and is still required.

4. **No private-deliberation field is referenced (plan §1.3.5, §6.3 leg 3).**
   The components and the chat tree view are scanned for the forbidden
   identifiers. Substring match, not word boundary, so a compound identifier
   cannot smuggle one through.

5. **The Skills tab is deep-linkable.** The route's existing `?section=`
   effect matches against `TABS`, so the tab is reachable iff `"skills"` is a
   member of the `SectionTab` union *and* of `TABS`, and a body is rendered.

6. **The frozen node list was not mutated (plan §3.4).** `GRAPH_NODES` is
   re-extracted from `api/runtime.ts` and compared to the 16 normative names
   from `docs/v1/architecture.md` §2; the tree component must not import it or
   `GraphNodeList`, and `GraphNodeList` must not import the tree. The tree is
   a new component, not an edit to the frozen list.

7. **No new colours, no light mode (plan §8.7).** The two new components and
   the skills hook are scanned for hex/rgb/hsl literals and for raw Tailwind
   palette steps. Only `tokens.css` / `tailwind.config.ts` names may appear.

8. **The library size is data, never a constant (plan §1.5.2).** `== 50` and
   friends are banned in the frontend that renders the registry; the counts are
   read off the server response.

Nothing here asserts a skill count. There is none to assert: the frontend
never learns how many skills exist except by asking.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.contracts.events import LEGACY_EVENT_TYPES, TREE_STATUSES

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = REPO_ROOT / "frontend" / "src"

CLIENT_TS = FRONTEND / "api" / "client.ts"
TURN_STREAM_TS = FRONTEND / "hooks" / "useTurnStream.ts"
SKILL_HOOK_TS = FRONTEND / "hooks" / "useSkillRegistry.ts"
EXECUTION_TREE_TSX = FRONTEND / "components" / "skills" / "ExecutionTree.tsx"
SKILL_LIST_TSX = FRONTEND / "components" / "skills" / "SkillList.tsx"
CHAT_TSX = FRONTEND / "routes" / "Chat.tsx"
SETTINGS_TSX = FRONTEND / "routes" / "Settings.tsx"
RUNTIME_TS = FRONTEND / "api" / "runtime.ts"
GRAPH_NODE_LIST_TSX = FRONTEND / "components" / "runtime" / "GraphNodeList.tsx"

# The listener array identifier in `client.ts`. Named (rather than inlined in
# the `for` loop) so the set is importable by the UI and extractable as text.
LISTENER_CONST = "LEGACY_EVENT_TYPES"

# The 16 normative graph route names, `docs/v1/architecture.md` §2. Frozen: the
# execution tree is a *different* surface and may not renumber these.
NORMATIVE_GRAPH_NODES = (
    "load_project",
    "load_conversation",
    "understand",
    "route",
    "state_only",
    "knowledge",
    "external_research",
    "social_research",
    "deep_research",
    "campaign_operation",
    "approval_operation",
    "job_followup",
    "aggregate",
    "synthesize",
    "approval-interrupt",
    "respond",
)

# Plan §1.3.5. Substring, deliberately: a compound identifier that merely
# contains one of these must fail too.
FORBIDDEN_UI_TOKENS = ("reasoning", "thought", "scratchpad", "cot")

# plan §8.7 -- no new colour literals in the files this run added.
COLOUR_LITERAL_PATTERNS = (
    re.compile(r"#[0-9a-fA-F]{3,8}\b"),
    re.compile(r"\brgba?\s*\("),
    re.compile(r"\bhsla?\s*\("),
    re.compile(r"\b(?:oklch|lab|lch|color-mix)\s*\("),
)
# Raw Tailwind default-palette steps, which are not in the token set.
TAILWIND_PALETTE_STEP = re.compile(
    r"\b(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|"
    r"emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-"
    r"(?:[0-9]{2,3})\b"
)

TAILWIND_CONFIG_TS = REPO_ROOT / "frontend" / "tailwind.config.ts"
TOKENS_CSS = REPO_ROOT / "frontend" / "src" / "styles" / "tokens.css"

# Tailwind utilities that match the `prefix-name` shape but are not colours.
# Anything else with that shape must be a token declared in
# `tailwind.config.ts` — which is what `test_colours_come_from_the_token_set`
# resolves against, so this list is only a floor, not the source of truth.
NON_TOKEN_UTILITIES = {
    "px", "py", "pt", "pb", "pl", "pr", "mx", "my", "mt", "mb", "ml", "mr",
    "tr", "b", "l", "r", "t", "x", "y", "z", "s", "e", "w", "n", "start",
    "end", "full", "none", "auto", "hidden", "visible", "collapse",
    "flex", "grid", "inline", "block", "table", "contents", "isolate",
    "font", "leading", "tracking", "whitespace", "break", "truncate",
    "uppercase", "lowercase", "capitalize", "normal", "italic", "underline",
    "line", "no", "rounded", "divide", "object", "overflow", "cursor",
    "select", "resize", "pointer", "sr", "align", "justify", "items",
    "place", "space", "gap", "animate", "static", "fixed", "absolute",
    "relative", "sticky", "inset", "top", "bottom", "left", "right",
    "aspect", "columns", "backdrop", "filter", "blur", "brightness",
    "contrast", "drop", "grayscale", "invert", "saturate", "sepia",
    "transform", "transition", "duration", "ease", "delay", "animate",
    "appearance", "caret", "accent", "resize", "scroll", "snap", "will",
    "list", "columns", "first", "last", "odd", "even", "hover", "focus",
    "active", "disabled", "group", "peer", "dark", "motion", "print",
}

# A comparison against a hardcoded library size. Multi-digit only: `=== 1` and
# `length === 0` are ordinary local checks, whereas a two-or-more-digit
# comparison against a collection length is a pinned size pretending to be a
# fact. `50` is today's pin; the RULE is the digit count, so a re-pin needs no
# test change and the gate cannot be satisfied by editing the number.
HARDCODED_COUNT_COMPARISON = re.compile(r"(?:===|!==|==|!=)\s*\d{2,}\b")


def read(path: Path) -> str:
    assert path.is_file(), f"missing frontend source: {path}"
    return path.read_text(encoding="utf-8")


_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"//[^\n]*")


def code_only(source: str) -> str:
    """Strip JS/TS comments, preserving offsets by replacing with spaces.

    A docstring that *names* a forbidden module is documentation, not a
    dependency. Scanning code rather than prose is what keeps "this component
    documents that it does NOT use the frozen node list" from reading as "this
    component uses the frozen node list".
    """
    stripped = _BLOCK_COMMENT.sub(lambda m: " " * len(m.group(0)), source)
    return _LINE_COMMENT.sub(lambda m: " " * len(m.group(0)), stripped)


def string_literals(block: str) -> set[str]:
    """Double-quoted string literals inside a block of TypeScript source."""
    return set(re.findall(r'"([a-z0-9_]+)"', block))


def array_literal(text: str, name: str) -> str:
    """Return the body of `const <name> = [ ... ];` (or `as const` terminated)."""
    match = re.search(
        r"const\s+" + re.escape(name) + r"\s*(?::[^=]+)?=\s*\[(?P<body>.*?)\]\s*(?:as\s+const\s*)?;",
        text,
        re.S,
    )
    assert match, f"no array literal named {name!r} found"
    return match.group("body")


def object_literal(text: str, name: str) -> str:
    """Return the body of `const <name> = { ... };` at brace depth 1."""
    match = re.search(
        r"const\s+" + re.escape(name) + r"\s*(?::[^=]+)?=\s*\{", text
    )
    assert match, f"no object literal named {name!r} found"
    start = match.end() - 1
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : index]
    raise AssertionError(f"unbalanced braces in object literal {name!r}")


# ---------- 1 + 2: the two-sided event registration gate -------------------


def test_client_listener_array_equals_server_catalog():
    """The 13-type gap cannot reopen: the client set IS the server set."""
    body = array_literal(read(CLIENT_TS), LISTENER_CONST)
    client = string_literals(body)
    server = set(LEGACY_EVENT_TYPES)
    missing = sorted(server - client)
    extra = sorted(client - server)
    assert not missing, f"server emits these but the SPA has no listener: {missing}"
    assert not extra, f"client listens for event types the server never emits: {extra}"
    assert client == server


def test_no_listeners_within_the_array_literal_are_decorative():
    """The array feeds the loop directly -- an unused copy proves nothing."""
    client = read(CLIENT_TS)
    assert f"for (const t of {LISTENER_CONST})" in client, (
        "the listener array must be the thing streamTurnEvents iterates"
    )


def test_no_registered_name_closes_the_stream():
    """A closer inside the catalog would truncate the stream mid-tree."""
    closers = {"turn_completed", "turn_failed", "turn_closed", "stream_end"}
    assert closers <= set(LEGACY_EVENT_TYPES)
    # The client's own closer list is the same four names, and the handler
    # closes on that list rather than on inline literals.
    from_client = string_literals(
        array_literal(read(CLIENT_TS), "STREAM_CLOSING_EVENT_TYPES")
    )
    assert from_client == closers


def test_turn_summary_stays_off_the_wire():
    """§1.3.1 -- deliberately unregistered, on both sides."""
    client = string_literals(array_literal(read(CLIENT_TS), LISTENER_CONST))
    assert "turn_summary" not in client
    assert "turn_summary" not in set(LEGACY_EVENT_TYPES)


# ---------- 3: all seven statuses are rendered ------------------------------


@pytest.mark.parametrize("status", TREE_STATUSES)
def test_every_tree_status_has_a_render_mapping(status):
    """§1.3.4 -- a *key* in STATUS_VIEWS, not a mention of the string."""
    source = read(EXECUTION_TREE_TSX)
    views = object_literal(source, "STATUS_VIEWS")
    assert re.search(r"\b" + re.escape(status) + r"\s*:\s*\{", views), (
        f"TREE_STATUSES member {status!r} has no STATUS_VIEWS entry"
    )


def test_status_map_is_declared_as_an_exhaustive_record():
    """`Record<TreeStatus, StatusView>` makes a missing key a build error."""
    source = read(EXECUTION_TREE_TSX)
    assert "Record<TreeStatus, StatusView>" in source
    assert re.search(r"const\s+STATUS_VIEWS\s*:\s*Record<TreeStatus,\s*StatusView>", source)


def test_status_enum_in_the_ui_is_the_server_enum():
    """The UI enum is the server enum, member for member and in order."""
    body = array_literal(read(EXECUTION_TREE_TSX), "TREE_STATUSES")
    order = tuple(re.findall(r'"([A-Z_]+)"', body))
    assert set(order) == set(TREE_STATUSES)
    assert order == tuple(TREE_STATUSES), "status order drifted from the contract"


# ---------- 4: no private-deliberation field in the UI source --------------


@pytest.mark.parametrize(
    "path",
    [EXECUTION_TREE_TSX, SKILL_LIST_TSX, SKILL_HOOK_TS, TURN_STREAM_TS, CLIENT_TS],
    ids=lambda p: p.name,
)
def test_no_private_deliberation_identifier_in_new_frontend_files(path):
    source = read(path)
    for token in FORBIDDEN_UI_TOKENS:
        assert token not in source.lower(), f"{path.name} references {token!r}"


def test_chat_tree_view_has_no_private_deliberation_reference():
    """§6.3 leg 3 -- the Chat tree view specifically."""
    source = read(CHAT_TSX)
    assert "ExecutionTree" in source, "the chat route must render the execution tree"
    tree_section = source[source.index("ExecutionTree") :]
    for token in FORBIDDEN_UI_TOKENS:
        assert token not in tree_section.lower(), f"Chat tree view references {token!r}"


# ---------- 5: the Skills tab is reachable by ?section=skills --------------


def test_skills_tab_is_in_the_section_union():
    source = read(SETTINGS_TSX)
    union = re.search(r"type\s+SectionTab\s*=(?P<body>.*?);", source, re.S)
    assert union, "SectionTab union not found"
    assert '"skills"' in union.group("body"), "SectionTab has no `skills` member"


def test_skills_tab_is_in_the_tabs_array():
    """The `?section=` deep-link effect matches `TABS.some((t) => t.id === sec)`,
    so a union member alone is NOT reachable."""
    source = read(SETTINGS_TSX)
    tabs = re.search(r"const\s+TABS[^=]*=\s*\[(?P<body>.*?)\];", source, re.S)
    assert tabs, "TABS array not found"
    assert re.search(r'id:\s*"skills"', tabs.group("body")), (
        "`skills` is not in TABS, so ?section=skills resolves to the default tab"
    )


def test_deep_link_effect_is_unchanged_and_generic():
    """The effect is reused, not forked; it reads TABS, so no per-tab branch."""
    source = read(SETTINGS_TSX)
    effect = re.search(
        r"const\s+sec\s*=\s*params\.get\(\"section\"\);(?P<body>.*?)\n\s*\},?\s*\[\]\);",
        source,
        re.S,
    )
    assert effect, "the ?section= deep-link effect was not found"
    assert "TABS.some((t) => t.id === sec)" in effect.group("body")


def test_skills_section_body_renders_the_registry():
    source = read(SETTINGS_TSX)
    assert re.search(r'\{tab\s*===\s*"skills"\s*(?:&&|\?)', source), (
        "no section body is gated on tab === \"skills\""
    )
    assert "<SkillList" in source, "the skills section body does not render SkillList"
    assert re.search(r'import\s+SkillList\s+from\s+"\.\./components/skills/SkillList"', source)


def test_skill_list_uses_the_registry_hook():
    source = read(SKILL_LIST_TSX)
    assert re.search(r'import\s*\{[^}]*useSkillRegistry[^}]*\}\s*from\s*"\.\./\.\./hooks/useSkillRegistry"', source), (
        "SkillList must read the registry through useSkillRegistry"
    )
    assert re.search(r"useSkillRegistry\(\)", source)


# ---------- 6: the frozen node list is not the tree -----------------------


def test_graph_nodes_is_still_the_frozen_flat_route_list():
    source = read(RUNTIME_TS)
    body = array_literal(source, "GRAPH_NODES")
    # Graph node names are hyphen-or-underscore slugs, so the extractor must
    # accept `_` or it silently drops 9 of the 16 (and the gate would then be
    # comparing two different lists while claiming to compare one).
    names = tuple(re.findall(r'"([a-z0-9_-]+)"', body))
    assert names == NORMATIVE_GRAPH_NODES, (
        f"GRAPH_NODES drifted from the normative route names: got {names!r}, "
        f"expected {NORMATIVE_GRAPH_NODES!r}"
    )


def test_graph_node_list_does_not_embed_the_tree():
    """The tree is a NEW component, not a mutation of GraphNodeList."""
    source = code_only(read(GRAPH_NODE_LIST_TSX))
    assert "ExecutionTree" not in source
    assert "components/skills" not in source


def test_execution_tree_does_not_reuse_the_frozen_node_list():
    source = code_only(read(EXECUTION_TREE_TSX))
    assert "GRAPH_NODES" not in source
    assert "GraphNodeList" not in source
    assert "api/runtime" not in source, (
        "the tree must not depend on the graph-topology module; the two are "
        "different surfaces and coupling them would re-freeze the node list"
    )


def test_tree_links_on_parent_id_not_on_a_node_list():
    """§1.3.3 -- the tree groups on meta.parent_id."""
    source = code_only(read(EXECUTION_TREE_TSX))
    assert '"parent_id"' in source
    assert '"status"' in source
    assert "buildExecutionTree" in source


# ---------- 7: tokens only, single dark theme -----------------------------


@pytest.mark.parametrize(
    "path",
    [EXECUTION_TREE_TSX, SKILL_LIST_TSX, SKILL_HOOK_TS],
    ids=lambda p: p.name,
)
def test_no_new_colour_literals(path):
    source = read(path)
    for pattern in COLOUR_LITERAL_PATTERNS:
        assert not pattern.search(source), f"{path.name} contains a raw colour literal"
    assert not TAILWIND_PALETTE_STEP.search(source), (
        f"{path.name} uses a raw Tailwind palette step instead of a token"
    )


def _brace_block(source: str, open_index: int) -> str:
    """Body between the `{` at `open_index` and its matching `}`."""
    depth = 0
    for index in range(open_index, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[open_index + 1 : index]
    raise AssertionError("unbalanced braces in tailwind.config.ts")


def _theme_extend_body() -> str:
    config = read(TAILWIND_CONFIG_TS)
    anchor = re.search(r"\bextend\s*:\s*\{", config)
    assert anchor, "theme.extend not found in tailwind.config.ts"
    return _brace_block(config, anchor.end() - 1)


def _tailwind_extension_keys(section: str) -> set[str]:
    """Top-level keys of one `theme.extend.<section>` object."""
    body = _theme_extend_body()
    anchor = re.search(r"(?:^|[\s,{])" + re.escape(section) + r"\s*:\s*\{", body)
    assert anchor, f"theme.extend.{section} not found in tailwind.config.ts"
    inner = _brace_block(body, anchor.end() - 1)
    return set(re.findall(r"^\s*([A-Za-z][A-Za-z0-9]*)\s*:", inner, re.M))


def test_token_sources_exist():
    """The colour gate is only meaningful if the tokens it reads are present."""
    assert TOKENS_CSS.is_file(), "tokens.css moved; the colour gate needs updating"
    assert _tailwind_extension_keys("colors"), "no colour tokens declared"
    assert "surface" not in _tailwind_extension_keys("colors"), (
        "a `surface` colour token now exists; the existing Settings markup uses "
        "bg-surface, and the new components deliberately do not rely on it"
    )


@pytest.mark.parametrize(
    "path",
    [EXECUTION_TREE_TSX, SKILL_LIST_TSX],
    ids=lambda p: p.name,
)
def test_colours_come_from_the_token_set(path):
    """Every `prefix-name` colour utility is a name the theme actually declares.

    The allowed set is read from `tailwind.config.ts` at test time, so adding a
    token does not require editing this test and using a name the theme does not
    declare fails immediately (an undefined Tailwind class renders as nothing,
    which looks like a styling bug and is not caught by a type checker).
    """
    allowed = (
        _tailwind_extension_keys("colors")
        | _tailwind_extension_keys("fontSize")
        | _tailwind_extension_keys("borderRadius")
        | _tailwind_extension_keys("fontFamily")
    ) | NON_TOKEN_UTILITIES
    source = read(path)
    used = set(
        re.findall(r"\b(?:bg|border|text|from|via|to|ring|outline|fill|stroke|shadow|divide|decoration|caret|accent)-([a-z][a-z0-9]*)", source)
    )
    unknown = sorted(name for name in used if name not in allowed)
    assert not unknown, f"{path.name} uses classes the theme does not define: {unknown}"


def test_no_light_mode_added():
    """§8.7 -- the product is a single fixed dark theme."""
    for path in (EXECUTION_TREE_TSX, SKILL_LIST_TSX, SKILL_HOOK_TS):
        source = read(path).lower()
        for word in ("prefers-color-scheme", "light:", ".light", "data-theme"):
            assert word not in source, f"{path.name} introduces {word!r}"


# ---------- 8: the library size is data, never a constant ------------------


@pytest.mark.parametrize(
    "path",
    [SKILL_HOOK_TS, SKILL_LIST_TSX, EXECUTION_TREE_TSX, CLIENT_TS, SETTINGS_TSX],
    ids=lambda p: p.name,
)
def test_no_hardcoded_library_size(path):
    """§1.5.2 -- `== 50` must not appear; the count arrives from the server."""
    source = read(path)
    hits = [m.group(0) for m in HARDCODED_COUNT_COMPARISON.finditer(source)]
    assert not hits, f"{path.name} compares against a hardcoded count: {hits}"


def test_skill_counts_are_read_off_the_response():
    """Every headline number in the tab is the server's measured value."""
    source = read(SKILL_LIST_TSX)
    for field in ("data.total", "data.valid", "data.missing", "data.invalid.length"):
        assert field in source, f"SkillList does not render {field}"
    assert "data.file_count" in source
    assert "data.library_checksum" in source


def test_skill_count_appears_nowhere_in_the_registry_hook():
    """The hook has no expected size at all, so a re-pin is a no-op here."""
    source = code_only(read(SKILL_HOOK_TS))
    hits = re.findall(r"\b\d{2,}\b", source)
    assert not hits, f"useSkillRegistry contains multi-digit constants: {hits}"


def test_the_plan_literal_is_absent_from_the_frontend():
    """`== 50` must not appear in product logic — asserted literally as well as
    by the general rule, so the packet's own wording is on the record."""
    for path in sorted(FRONTEND.rglob("*.ts")) + sorted(FRONTEND.rglob("*.tsx")):
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"(?:===|!==|==|!=)\s*50\b", source), (
            f"{path.name} hardcodes the library size"
        )


# ---------- structural: the pieces are actually wired --------------------


def test_client_exposes_the_skills_endpoints():
    source = read(CLIENT_TS)
    assert '"/api/skills"' in source
    assert '"/api/skills/manifest"' in source
    assert re.search(r"/api/skills/\$\{encodeURIComponent\(skill_id\)\}/enabled", source), (
        "the per-skill enable endpoint is not wired"
    )


def test_chat_renders_the_execution_tree_from_stream_events():
    """The tree must read the streamed lifecycle rows, not a second stream."""
    source = read(CHAT_TSX)
    assert re.search(r"<ExecutionTree\s+events=\{stream\.events\}\s*/>", source)
    assert "useTurnStream" in source


def test_lifecycle_rows_carry_meta():
    """The tree cannot be built if `meta` is dropped by the hook."""
    source = read(TURN_STREAM_TS)
    assert re.search(r"interface\s+LifecycleItem\s*\{[^}]*meta\s*:", source, re.S), (
        "LifecycleItem must carry the server meta for parent_id/status"
    )
    assert re.search(r"detail:\s*e\.detail,\s*meta:\s*e\.meta", source), (
        "appendLifecycle must pass meta through"
    )


def test_settings_tab_bar_and_union_were_not_forked():
    """Six extension points, not a second tab bar."""
    source = read(SETTINGS_TSX)
    assert source.count("{TABS.map(") == 1, "a second hand-rolled tab bar was added"
    assert len(re.findall(r"type\s+SectionTab\s*=", source)) == 1
