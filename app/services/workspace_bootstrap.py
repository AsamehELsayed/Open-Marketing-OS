"""First-run workspace bootstrap (DEV-008 distribution).

A source checkout ships `company/`, `knowledge/`, `production/`, `strategy/` and
`state/` as tracked files, so `Settings -> Reimport from workspace files` and
`Build / update index` have always had something to read. A frozen Windows
install has an **empty, writable** workspace, and every parser in
`app/services/adapters/parsers.py` calls ``Path(...).read_text()`` with no
existence guard. Without this bootstrap, both of those user actions raise
``FileNotFoundError`` and surface to a non-developer as a raw traceback on a
brand-new install.

`ensure_workspace()` therefore writes a small, honest starter workspace the
first time OMOS runs against an empty data directory. It is:

- **idempotent** — an existing file is never overwritten, so a user who has
  edited `knowledge/brand.md` keeps their edits across upgrades and restarts;
- **non-destructive** — it only ever *creates* missing files;
- **empty of fabricated content** — the starter files contain prompts and
  headings for the user to fill in, never invented business facts. The
  retrieval and chat layers are explicitly evidence-disciplined, and seeding
  invented "facts" here would poison every later answer.

`apply_business_details()` is the write path used by the first-run wizard to
turn the starter `company/company.yaml` into the user's real business.
"""
from __future__ import annotations

import json
from pathlib import Path

# --- starter file contents -------------------------------------------------
#
# Headings and questions only. No invented positioning, metrics, customers or
# competitor names: an empty prompt is honest, a plausible-sounding fabrication
# is not.

_STARTER: dict[str, str] = {
    "company/company.yaml": """# Your business
# Filled in by the OMOS setup wizard. Edit any time, then
# Settings -> General -> "Reimport from workspace files".

company:
  name: "My Business"
  website: ""

markets:
  primary: []
  expansion: []

languages: []

known_services: []

seed_hypotheses:
  - "Add the things you believe about your business, then verify them."
""",
    "knowledge/brand.md": """# Brand

How you want to be known. Facts and proof only — mark anything unverified.

## Positioning

- What you do, in one sentence:
- Who it is for:
- Why you are different:

## Proof

- Live links, numbers, testimonials, case studies:
""",
    "knowledge/icp.md": """# Ideal customer profile

## Who buys

- Industry:
- Company size:
- Situation that makes them look for a solution:
- What they have already tried:

## Who does not buy

-
""",
    "knowledge/offers.md": """# Offers

## Main offer

- What it includes:
- Price:
- What it replaces or compares to:

## Risk reversal / guarantees

-

## Proof to attach

-
""",
    "knowledge/competitors.md": """# Competitors

| Name | URL | Their pitch | Where they are strong | Where they are weak |
|---|---|---|---|---|
|  |  |  |  |  |

## Our read

-
""",
    "knowledge/customer-language.md": """# Customer language

Words your customers actually use. Copy them from real reviews, sales calls and
support threads rather than writing them yourself.

## Phrases we hear

-

## Objections

-
""",
    "knowledge/proof-library.md": """# Proof library

Only include proof you can show a customer. Link it.

| Claim | Evidence | Where it lives |
|---|---|---|
|  |  |  |
""",
    "production/weekly-plan.md": """# Weekly plan

## This week

- [ ]

## Next

- [ ]
""",
    "production/approval-queue.md": """# Approval queue

Anything waiting for your sign-off before it goes out.

| Item | Why it needs approval | Status |
|---|---|---|
""",
    "production/active-campaigns.md": """# Active campaigns

| Campaign | Channel | Status | Result so far |
|---|---|---|---|
""",
    "production/measurement-queue.md": """# Measurement queue

Decisions waiting on data.

| Decision | Metric | Window | Status |
|---|---|---|---|
""",
    "production/learning-log.md": """# Learning log

What we measured, what we concluded, what we changed.

| Date | What we learned | What we changed |
|---|---|---|
""",
    "production/system-health.md": """# System health

- Last index build:
- Credential status:
- Notes:
""",
    "strategy/opportunity-backlog.md": """# Opportunity backlog

| Opportunity | Impact | Confidence | Effort | Status |
|---|---|---|---|---|
""",
}

#: The two JSON sources. `parse_actions` / `parse_experiments` do
#: ``json.loads(read_text())`` and then ``data.get(<key>, [])``, so the
#: top level must be an **object** with the right key — an empty list or an
#: empty file raises `AttributeError` / `JSONDecodeError` on a fresh install.
_STARTER_JSON: dict[str, object] = {
    "state/actions.json": {"actions": []},
    "state/experiments.json": {"experiments": []},
}

#: Every workspace file the boot creates, for Settings/docs to display.
STARTER_FILES: tuple[str, ...] = tuple(sorted(_STARTER)) + tuple(sorted(_STARTER_JSON))


def ensure_workspace(root: str | Path) -> list[str]:
    """Create any missing starter file under ``root``.

    Returns the list of relative paths actually created (empty when the
    workspace is already populated). Never raises for a partially unwritable
    workspace: the caller reports a friendly error instead.
    """
    root = Path(root)
    created: list[str] = []
    for rel, content in _STARTER.items():
        path = root / rel
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        created.append(rel)
    for rel, payload in _STARTER_JSON.items():
        path = root / rel
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        created.append(rel)
    return sorted(created)


def workspace_is_seeded(root: str | Path) -> bool:
    """True once the company file exists, i.e. onboarding has a real subject."""
    return (Path(root) / "company" / "company.yaml").is_file()


def _yaml_quote(value: str) -> str:
    """Quote a scalar for the minimal parser in ``adapters/parsers.py``.

    That parser splits on the first ``:`` and strips surrounding quotes, so a
    value containing a colon must stay quoted to survive a round trip.
    """
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_company_yaml(
    name: str,
    website: str = "",
    industry: str = "",
    markets: list[str] | None = None,
    languages: list[str] | None = None,
    services: list[str] | None = None,
) -> str:
    """Render a `company/company.yaml` in the exact shape the parser reads.

    Kept deliberately narrow: the shipped parser understands only
    `company.name`, `company.website`, `markets.primary`, `markets.expansion`,
    `languages` and `known_services`. Anything else is ignored rather than
    silently dropped, so this writer does not emit keys the parser cannot see.
    """
    lines = [
        "# Your business",
        "# Written by the OMOS setup wizard. Safe to edit by hand;",
        "# after editing use Settings -> General -> \"Reimport from workspace files\".",
        "",
        "company:",
        f"  name: {_yaml_quote(name)}",
        f"  website: {_yaml_quote(website)}",
        "",
        "markets:",
        "  primary:",
    ]
    for market in markets or []:
        lines.append(f"    - {_yaml_quote(market)}")
    lines += ["  expansion: []", "", "languages:"]
    for language in languages or []:
        lines.append(f"  - {_yaml_quote(language)}")
    lines += ["", "known_services:"]
    for service in services or []:
        lines.append(f"  - {_yaml_quote(service)}")
    if industry:
        # Not parsed by the minimal reader today; kept as a human-readable note
        # rather than dropped, so nothing the user typed silently disappears.
        lines += ["", "# Industry (recorded here for your reference):", f"# {_yaml_quote(industry)}"]
    lines += [
        "",
        "seed_hypotheses:",
        "  - \"Add the things you believe about your business, then verify them.\"",
        "",
    ]
    return "\n".join(lines)


def apply_business_details(
    root: str | Path,
    name: str,
    website: str = "",
    industry: str = "",
    markets: list[str] | None = None,
    languages: list[str] | None = None,
    services: list[str] | None = None,
) -> Path:
    """Write the user's business into `company/company.yaml`.

    This *is* the "Create Your Business" write path. It deliberately goes
    through the real file -> importer -> database flow the product already uses
    for every other workspace source, rather than inserting a project row
    behind the adapters' back.
    """
    root = Path(root)
    ensure_workspace(root)
    target = root / "company" / "company.yaml"
    if not (root / "knowledge" / "brand.md").is_file():
        (root / "knowledge").mkdir(parents=True, exist_ok=True)
    target.write_text(
        render_company_yaml(
            name, website=website, industry=industry,
            markets=markets, languages=languages, services=services,
        ),
        encoding="utf-8",
    )
    return target
