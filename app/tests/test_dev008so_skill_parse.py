"""DEV-008-SKILLS-OPS / W1 -- the skill parser, proven against the real corpus.

What this file has to establish
-------------------------------
Three claims, each of which is easy to make and easy to fake, so each is measured
here rather than asserted as a remembered number.

1. **The stdlib fallback is sufficient.** PyYAML is a declared, locked, shipped
   dependency, so "the repo has no PyYAML guarantee" is false. The fallback exists
   for the *packaging* risk (``packaging/omos.spec`` hides ``yaml.cython`` but not
   ``yaml``), and the only honest justification for shipping it is that it parses
   this corpus identically. So every vendored ``SKILL.md`` is parsed **twice**,
   with each parser's real result, and the two are compared. If PyYAML were
   unavailable the test would skip, not pass -- an absent parser must never be
   reported as agreement.

2. **Both quoting styles are handled.** Measured: 22 descriptions are plain and 28
   are YAML double-quoted. The split is *measured* from the tree by
   :func:`describe_quoting`, and the two measured groups are asserted non-empty,
   so the test fails if upstream ever becomes uniform (which would mean the
   quoting branch is untested) or if the split is a hardcoded constant. The
   22/28 figures are printed, not baked in.

3. **A plain description never contains ``": "``.** That invariant is what makes
   the frozen split rule total. It is asserted per file, and the three files whose
   descriptions *do* contain a colon are asserted to be double-quoted, where the
   colon is inside quotes and therefore legal.

The Related Skills section has three real shapes upstream (bold slug, backticked
slug inside bold, markdown table) and two files have no section at all. All three
extractors are exercised against a real file each, and the files with no section
are asserted to yield an empty tuple rather than raising.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.services.skills import parser as skill_parser
from app.services.skills.parser import (
    SkillParseError,
    describe_quoting,
    parse_description,
    parse_frontmatter,
    parse_frontmatter_both,
    parse_name,
    parse_version,
    partition_related,
    read_evals,
    related_skills_from_description,
    related_skills_from_section,
    split_frontmatter,
    unquote_description,
)
from app.services.skills.records import DESCRIPTION_MAX_CHARS, TRIGGER_MAX_CHARS

REPO_ROOT = Path(__file__).resolve().parents[2]
LIBRARY_ROOT = REPO_ROOT / ".agents" / "skills"

pytestmark = pytest.mark.skipif(
    not LIBRARY_ROOT.is_dir(),
    reason=(
        "the vendored marketing-skills library is not present at "
        ".agents/skills; nothing here can be measured without it"
    ),
)

SKILL_DIRS = sorted(p for p in LIBRARY_ROOT.iterdir() if p.is_dir())
SKILL_IDS = [p.name for p in SKILL_DIRS]


def read_text(skill_id: str) -> str:
    raw = (LIBRARY_ROOT / skill_id / "SKILL.md").read_bytes()
    return raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def load_all() -> dict[str, str]:
    return {skill_id: read_text(skill_id) for skill_id in SKILL_IDS}


DOCS = load_all()

requires_pyyaml = pytest.mark.skipif(
    skill_parser.parse_frontmatter_pyyaml(DOCS[SKILL_IDS[0]]) is None,
    reason="PyYAML is not importable; the two-parser comparison cannot run",
)


# --------------------------------------------------------------------- #
# 1. frontmatter extraction                                               #
# --------------------------------------------------------------------- #


def test_library_is_present_and_non_trivial():
    """Guard against a vacuous pass: an empty corpus proves nothing."""
    assert SKILL_DIRS, f"no skill directories under {LIBRARY_ROOT}"
    assert len(SKILL_IDS) == len(set(SKILL_IDS)), "duplicate directory names"


def test_every_skill_md_opens_with_a_delimiter():
    for skill_id in SKILL_IDS:
        frontmatter, body = split_frontmatter(DOCS[skill_id])
        assert frontmatter, f"{skill_id}: empty frontmatter"
        assert body.strip(), f"{skill_id}: empty body after frontmatter"


def test_undelimited_document_is_refused_with_a_named_reason():
    with pytest.raises(SkillParseError) as excinfo:
        split_frontmatter("name: x\ndescription: y\n")
    assert excinfo.value.reason_code == "FRONTMATTER_UNDELIMITED"


def test_unterminated_document_is_refused_with_a_named_reason():
    with pytest.raises(SkillParseError) as excinfo:
        split_frontmatter("---\nname: x\ndescription: y\n")
    assert excinfo.value.reason_code == "FRONTMATTER_UNDELIMITED"


def test_bom_prefixed_document_still_parses():
    body = DOCS[SKILL_IDS[0]]
    frontmatter, _ = parse_frontmatter("\ufeff" + body)
    assert frontmatter["name"] == SKILL_IDS[0]


def test_every_frontmatter_has_exactly_the_measured_key_set():
    """W0 §3 measured three top-level keys. A fourth key is new upstream data."""
    for skill_id, text in DOCS.items():
        frontmatter, parser_name = parse_frontmatter(text)
        assert set(frontmatter) == {"name", "description", "metadata"}, (
            f"{skill_id} (parser={parser_name}) keys={sorted(frontmatter)}"
        )
        assert set(frontmatter["metadata"]) == {"version"}, skill_id


# --------------------------------------------------------------------- #
# 2. the two parsers agree -- the frozen test obligation                   #
# --------------------------------------------------------------------- #


@requires_pyyaml
@pytest.mark.parametrize("skill_id", SKILL_IDS)
def test_both_parsers_produce_identical_frontmatter(skill_id):
    """parse(pyyaml) == parse(stdlib) for every vendored SKILL.md.

    This is the proof the stdlib fallback is sufficient for the real corpus. It
    compares the two raw results; it does not route both through a normaliser
    that could hide a disagreement.
    """
    both = parse_frontmatter_both(DOCS[skill_id])
    assert both["pyyaml"] is not None, "PyYAML declined the document"
    assert both["pyyaml"] == both["stdlib"], f"{skill_id}: parsers disagree"


@requires_pyyaml
def test_parsed_fields_are_equal_under_both_parsers(skill_id="__all__"):
    """Field-level equality too, not just dict equality."""
    for skill_id in SKILL_IDS:
        text = DOCS[skill_id]
        pyyaml_fm = skill_parser.parse_frontmatter_pyyaml(text)
        stdlib_fm = skill_parser._stdlib_frontmatter(text)
        assert parse_name(pyyaml_fm) == parse_name(stdlib_fm), skill_id
        assert parse_version(pyyaml_fm) == parse_version(stdlib_fm), skill_id
        assert parse_description(pyyaml_fm) == parse_description(stdlib_fm), skill_id


def test_pyyaml_is_actually_the_parser_used_when_available():
    frontmatter, parser_name = parse_frontmatter(DOCS[SKILL_IDS[0]])
    if skill_parser.parse_frontmatter_pyyaml(DOCS[SKILL_IDS[0]]) is not None:
        assert parser_name == "pyyaml", "PyYAML is installed but not preferred"
    else:
        assert parser_name == "stdlib"
    assert frontmatter["name"] == SKILL_IDS[0]


# --------------------------------------------------------------------- #
# 3. the measured quoting split                                           #
# --------------------------------------------------------------------- #


def test_quoting_split_is_measured_and_both_groups_are_non_empty():
    """22 plain / 28 double-quoted, measured here rather than remembered.

    The two literal counts are intentionally *not* the assertion. If they were,
    a re-pin that shifted the split would fail a test about something the code
    does not care about, and the real property -- that both branches are
    exercised -- would go unchecked. The measured numbers are printed so the
    handoff can quote them.
    """
    measured: dict[str, list[str]] = {}
    for skill_id, text in DOCS.items():
        measured.setdefault(describe_quoting(text), []).append(skill_id)

    assert set(measured) == {"plain", "double-quoted"}, sorted(measured)
    assert measured["plain"], "no plain-style description in the corpus"
    assert measured["double-quoted"], "no double-quoted description in the corpus"
    print(
        f"\nquoting split: plain={len(measured['plain'])} "
        f"double-quoted={len(measured['double-quoted'])}"
    )


@pytest.mark.parametrize("skill_id", SKILL_IDS)
def test_plain_descriptions_never_contain_colon_space(skill_id):
    """The invariant that makes the frozen split rule total.

    A plain scalar containing ``": "`` would be cut at the wrong place, so this
    is asserted per file rather than assumed. Files whose description *does*
    contain a colon must be double-quoted, where the colon is inside the quotes
    and therefore not a key separator.
    """
    raw = re.search(
        r"^description:[ \t]*(.*)$", DOCS[skill_id].split("\n---\n", 1)[0], re.M
    ).group(1)
    if describe_quoting(DOCS[skill_id]) == "plain":
        assert '": "' not in raw, f"{skill_id}: plain description contains ': '"
    else:
        # Legal only because the colon is inside the quoted scalar.
        assert raw.startswith('"') and raw.endswith('"'), skill_id


def test_colon_bearing_plain_style_would_be_mangled_by_naive_stripping():
    """Why the plain branch must not ``strip('"')`` first.

    22 of the plain descriptions contain a double quote. Stripping quotes before
    the split would eat the first and last legitimate character. This asserts the
    round-trip on real corpus text, so a future "simplification" fails loudly.
    """
    plain_with_quotes = [
        skill_id
        for skill_id, text in DOCS.items()
        if describe_quoting(text) == "plain"
        and '"' in re.search(
            r"^description:[ \t]*(.*)$", text.split("\n---\n", 1)[0], re.M
        ).group(1)
    ]
    assert plain_with_quotes, "no plain description contains a quote"
    for skill_id in plain_with_quotes:
        text = DOCS[skill_id]
        raw = re.search(
            r"^description:[ \t]*(.*)$", text.split("\n---\n", 1)[0], re.M
        ).group(1)
        assert unquote_description(raw) == parse_description(
            skill_parser.parse_frontmatter_pyyaml(text) or {}
        )[0], skill_id


def test_double_quoted_escapes_decode():
    assert unquote_description('"a \\"quoted\\" word"') == 'a "quoted" word'
    assert unquote_description('"back\\\\slash"') == "back\\slash"


def test_plain_description_tail_is_taken_after_the_first_colon_space():
    Q = '"'
    assert unquote_description('Use when the user says "x", "y"') == (
        'Use when the user says "x", "y"'
    )
    # Only the FIRST `": "` (4 chars: quote colon space quote) splits, so later
    # quoted phrases survive intact.
    assert unquote_description('head "a", "b" tail "c"') == (
        'head "a", "b" tail "c"'
    )
    # Second legal plain form: a trailing `":"` (3 chars: quote colon quote).
    # Implemented literally per plan §1.2.2, which drops the final two characters
    # and therefore leaves the opening quote behind. No vendored description uses
    # this form, so it is unreachable in practice; see the KNOWN DEFECT note in
    # workers/w1.md. Asserted here so the literal behaviour is pinned rather than
    # whatever it happens to be.
    trailing = "see cro" + Q + ":" + Q
    assert unquote_description(trailing) == "see cro" + Q


def test_trailing_terminator_form_is_unused_by_the_real_corpus():
    """The quirky branch of the frozen rule is not load-bearing today."""
    Q = '"'
    users = [
        skill_id
        for skill_id, text in DOCS.items()
        if describe_quoting(text) == "plain"
        and re.search(
            r"^description:[ \t]*(.*)$", text.split("\n---\n", 1)[0], re.M
        )
        .group(1)
        .rstrip()
        .endswith(Q + ":" + Q)
    ]
    assert users == [], f"plain descriptions now use the trailing form: {users}"


# --------------------------------------------------------------------- #
# 4. name / version / description field rules                             #
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("skill_id", SKILL_IDS)
def test_name_equals_directory_name(skill_id):
    frontmatter, _ = parse_frontmatter(DOCS[skill_id])
    assert parse_name(frontmatter) == skill_id


@pytest.mark.parametrize("skill_id", SKILL_IDS)
def test_version_is_semver(skill_id):
    frontmatter, _ = parse_frontmatter(DOCS[skill_id])
    assert re.match(r"^\d+\.\d+\.\d+$", parse_version(frontmatter)), skill_id


def test_missing_version_is_named_not_swallowed():
    with pytest.raises(SkillParseError) as excinfo:
        parse_version({"name": "x", "description": "y"})
    assert excinfo.value.reason_code == "VERSION_MISSING"


def test_malformed_version_is_named_not_swallowed():
    with pytest.raises(SkillParseError) as excinfo:
        parse_version({"metadata": {"version": "2.1"}})
    assert excinfo.value.reason_code == "VERSION_MALFORMED"


def test_missing_description_is_named_not_swallowed():
    with pytest.raises(SkillParseError) as excinfo:
        parse_description({"name": "x"})
    assert excinfo.value.reason_code == "DESCRIPTION_MISSING"


@pytest.mark.parametrize("skill_id", SKILL_IDS)
def test_description_is_collapsed_and_bounded(skill_id):
    frontmatter, _ = parse_frontmatter(DOCS[skill_id])
    description, truncated = parse_description(frontmatter)
    assert description == description.strip(), skill_id
    assert "  " not in description, f"{skill_id}: description was not collapsed"
    assert len(description) <= DESCRIPTION_MAX_CHARS, skill_id
    # Truncation must be reported, never silent: a clamped description that
    # reports False is a lie about the data.
    if truncated:
        assert len(description) == DESCRIPTION_MAX_CHARS, skill_id


def test_description_truncation_is_reported_not_hidden():
    long_value = "x" * (DESCRIPTION_MAX_CHARS + 500)
    text, truncated = parse_description({"description": long_value})
    assert truncated is True
    assert len(text) == DESCRIPTION_MAX_CHARS
    short, short_truncated = parse_description({"description": "short"})
    assert short_truncated is False


# --------------------------------------------------------------------- #
# 5. Related Skills -- three shapes, two absences                          #
# --------------------------------------------------------------------- #


def test_all_three_related_shapes_extract_from_a_real_file():
    """One real file per shape, so no extractor is dead code.

    The naive single-bullet regex covers 46 of 50 and silently loses the other
    two plus the two files with no section -- a degradation that is invisible
    until routing quality quietly drops.
    """
    by_shape: dict[str, list[str]] = {"backtick-in-bold": [], "table": [], "bold": []}
    for skill_id, text in DOCS.items():
        section_slugs = set(related_skills_from_section(text))
        if not section_slugs:
            continue
        raw_section = text.split("## Related Skills", 1)
        if skill_parser._RELATED_PATTERNS[0].search(
            text[text.index("\n", text.lower().index("related skills")) :]
        ):
            by_shape["backtick-in-bold"].append(skill_id)
        if skill_parser._RELATED_PATTERNS[1].search(
            text[text.index("\n", text.lower().index("related skills")) :]
        ):
            by_shape["table"].append(skill_id)
        if skill_parser._RELATED_PATTERNS[2].search(
            text[text.index("\n", text.lower().index("related skills")) :]
        ):
            by_shape["bold"].append(skill_id)

    print(
        "\nrelated-skills shapes: "
        + ", ".join(f"{k}={len(v)}" for k, v in by_shape.items())
    )
    for shape, ids in by_shape.items():
        assert ids, f"no vendored file uses the {shape!r} Related Skills shape"
        for skill_id in ids:
            assert related_skills_from_section(DOCS[skill_id]), skill_id


def test_lowercase_heading_is_accepted():
    """``marketing-plan/SKILL.md:262`` writes ``## Related skills``."""
    text = DOCS["marketing-plan"]
    assert re.search(r"^##\s*[Rr]elated\s+[Ss]kills\s*$", text, re.M)
    assert "## Related skills" in text or "## Related Skills" in text
    assert related_skills_from_section(text), "lowercase heading lost every slug"


def test_table_shape_is_accepted():
    text = DOCS["customer-research"]
    assert "|" in text
    assert related_skills_from_section(text), "table shape lost every slug"


def test_files_without_a_related_section_yield_empty_not_an_error():
    without = [sid for sid in DOCS if not related_skills_from_section(DOCS[sid])]
    print(f"\nfiles with no Related Skills section: {len(without)} -> {without}")
    for skill_id in without:
        assert related_skills_from_section(DOCS[skill_id]) == ()


def test_self_reference_is_excluded():
    for skill_id in DOCS:
        resolved, _ = partition_related(
            (skill_id,), self_id=skill_id, registered={skill_id}
        )
        assert resolved == ()


def test_unresolved_references_are_carried_not_dropped():
    """A broken cross-reference is a real defect and must stay visible."""
    resolved, unresolved = partition_related(
        ("cro", "not-a-real-skill", "also-not-real"),
        self_id="ab-testing",
        registered={"ab-testing", "cro"},
    )
    assert resolved == ("cro",)
    assert unresolved == ("also-not-real", "not-a-real-skill")
    assert partition_related(("z", "a"), self_id="x", registered=set())[0] == ()


def test_description_tail_cross_references_are_mined():
    for skill_id in DOCS:
        frontmatter, _ = parse_frontmatter(DOCS[skill_id])
        description, _ = parse_description(frontmatter)
        tails = related_skills_from_description(description)
        assert all(re.match(r"^[a-z0-9][a-z0-9-]*$", t) for t in tails), skill_id
    assert related_skills_from_description(
        "For page-level CRO, see cro. For measurement, see analytics."
    ) == ("analytics", "cro")


def test_unresolved_description_tails_exist_in_the_real_corpus():
    """The corpus really does contain dangling refs, and they are surfaced.

    If this ever returns nothing the field is untested; if it ever returns a
    registered slug the partition is broken.
    """
    registered = set(SKILL_IDS)
    dangling: dict[str, list[str]] = {}
    for skill_id, text in DOCS.items():
        frontmatter, _ = parse_frontmatter(text)
        description, _ = parse_description(frontmatter)
        _, unresolved = partition_related(
            related_skills_from_description(description),
            self_id=skill_id,
            registered=registered,
        )
        for slug in unresolved:
            dangling.setdefault(slug, []).append(skill_id)
    print(f"\ndangling description-tail refs: {dangling}")
    assert dangling, "no dangling refs: the unresolved path is untested"
    for slug in dangling:
        assert slug not in registered


# --------------------------------------------------------------------- #
# 6. triggers are mined, never authored                                   #
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("skill_id", SKILL_IDS)
def test_triggers_come_from_the_upstream_evals_file(skill_id):
    triggers, eval_count, warning = read_evals(
        LIBRARY_ROOT / skill_id / "evals" / "evals.json"
    )
    assert warning is None, f"{skill_id}: {warning}"
    assert eval_count > 0, skill_id
    assert triggers, skill_id
    assert all(len(t) <= TRIGGER_MAX_CHARS for t in triggers), skill_id
    assert len(set(triggers)) == len(triggers), f"{skill_id}: triggers not deduped"

    document = json.loads(
        (LIBRARY_ROOT / skill_id / "evals" / "evals.json").read_text(encoding="utf-8")
    )
    upstream = [
        e["prompt"].strip()
        for e in document["evals"]
        if isinstance(e.get("prompt"), str) and e["prompt"].strip()
    ]
    for trigger in triggers:
        # A trigger is an upstream prompt or the leading slice of one, so the
        # check is "is this text upstream's", not "is this byte-identical".
        assert any(prompt.startswith(trigger) for prompt in upstream), (
            f"{skill_id}: trigger is not upstream text"
        )


def test_missing_evals_file_yields_empty_triggers_and_a_warning():
    """A missing corpus is a packaging defect, not an unparseable skill."""
    triggers, count, warning = read_evals(
        LIBRARY_ROOT / "definitely-not-a-skill" / "evals" / "evals.json"
    )
    assert triggers == ()
    assert count == 0
    assert warning and "absent" in warning


def test_malformed_evals_file_yields_empty_triggers_and_a_warning(tmp_path):
    bad = tmp_path / "evals.json"
    bad.write_text("{not json", encoding="utf-8")
    triggers, count, warning = read_evals(bad)
    assert (triggers, count) == ((), 0)
    assert warning and "unreadable" in warning

    wrong_shape = tmp_path / "shape.json"
    wrong_shape.write_text(json.dumps({"skill_name": "x"}), encoding="utf-8")
    triggers, count, warning = read_evals(wrong_shape)
    assert (triggers, count) == ((), 0)
    assert warning and "evals array" in warning


def test_every_skill_has_an_evals_file():
    missing = [
        sid for sid in SKILL_IDS if not (LIBRARY_ROOT / sid / "evals" / "evals.json").is_file()
    ]
    assert missing == [], f"skills without an upstream eval corpus: {missing}"


# --------------------------------------------------------------------- #
# 7. the stdlib reader refuses what it cannot represent                   #
# --------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "frontmatter, label",
    [
        ("name: x\nvalue: [1, 2, 3]", "flow collection"),
        ("name: x\nvalue: |\n  block scalar", "block scalar"),
        ("name: x\nother: &anchor 1", "anchor"),
        ("name: x\nmetadata:\n  nested:\n    deeper: 1", "nesting deeper than one level"),
        ("name: x\nvalue: *alias", "alias reference"),
        ("name: x\nname: y", "duplicate key"),
        ("name: x\njust some text", "line that is not key: scalar"),
        ("name: x\nmetadata:", "empty metadata map"),
    ],
)
def test_stdlib_reader_refuses_unsupported_yaml(frontmatter, label):
    """Refusing is the correct behaviour: a wrong playbook is worse than none.

    Anchors, flow collections, block scalars, multi-line plain scalars and deeper
    nesting all raise rather than being guessed at.
    """
    text = f"---\n{frontmatter}\n---\n\n# body\n"
    with pytest.raises(SkillParseError) as excinfo:
        skill_parser._stdlib_frontmatter(text)
    assert excinfo.value.reason_code == "FRONTMATTER_UNPARSEABLE", label


def test_stdlib_reader_accepts_comments_and_blank_lines():
    text = "---\n# a comment\nname: x\n\ndescription: hello there\n---\n\n# body\n"
    parsed = skill_parser._stdlib_frontmatter(text)
    assert parsed["name"] == "x"
    assert parsed["description"] == "hello there"


def test_stdlib_reader_matches_pyyaml_on_a_synthetic_document():
    text = (
        "---\n"
        "name: sample-skill\n"
        'description: "When the user mentions \\"a thing\\"."\n'
        "metadata:\n"
        "  version: 1.2.3\n"
        "---\n"
    )
    assert skill_parser._stdlib_frontmatter(text) == (
        skill_parser.parse_frontmatter_pyyaml(text)
    )
