"""DEV-008-SKILLS-OPS (W2) — provenance and distribution gate.

The pinned upstream marketing skills library is MIT. That fact was known only
outside this repository: `NOTICE` had no row for it, the licence audit recorded
it as unresolved, and the public export shipped zero bytes of it. None of that
was a licence problem — MIT permits redistribution as long as the notice travels
with the copies. It was a **record** problem, and an unrecorded redistribution
is a redistribution anyway once the export starts including the files.

So this file is the gate that makes the recording real rather than written down.
It asserts four things, each of which is a way the previous state could come
back:

1. The upstream `LICENSE` is vendored **verbatim** at `.agents/LICENSE` — the
   bytes are re-hashed from disk, not asserted from a copy of the file.
2. `NOTICE` names the repository, the tag, the commit and the SPDX id, so a
   reader can re-derive the grant.
3. The public export includes the library **and** its licence, and withholds
   `.agents/product-marketing.md`, which is OMOS-authored and is not upstream.
   The withholding is enforced by the exporter itself, not only by this list.
4. The Windows payload carries the same two entries through the same narrow
   rule, and `packaging/omos.iss` needed no change because it recurses the
   payload.

Nothing here hardcodes a skill count. Every count is derived from the working
tree on each run, so a re-pin changes the number and the gate follows it.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPORTER_SCRIPT = REPO_ROOT / "scripts" / "package" / "export_public_repo.py"
SPEC_FILE = REPO_ROOT / "packaging" / "omos.spec"
ISS_FILE = REPO_ROOT / "packaging" / "omos.iss"
AGENTS_LICENSE = REPO_ROOT / ".agents" / "LICENSE"
SKILLS_ROOT = REPO_ROOT / ".agents" / "skills"

#: The upstream `LICENSE` at the pinned commit
#: `5b2c0007766c6a1cf1d53fd8fc73e979e0821022`, fetched from
#: `raw.githubusercontent.com/coreyhaines31/marketingskills/<commit>/LICENSE`.
#:
#: This is a **pin**, in the same sense as a checksum in a lockfile: it is data
#: that records what the vendored bytes are, and it is compared against the
#: bytes on disk rather than replacing a measurement. The byte count is the
#: `Content-Length` upstream reported for that file.
UPSTREAM_LICENSE_SHA256 = "b70d71e24e40fce5da8f4b6f9cd862096a048e433db7f3c8cac5e348e6d34591"
UPSTREAM_LICENSE_BYTES = 1069
UPSTREAM_REPO = "coreyhaines31/marketingskills"
UPSTREAM_TAG = "v2.11.1"
UPSTREAM_COMMIT = "5b2c0007766c6a1cf1d53fd8fc73e979e0821022"
UPSTREAM_COPYRIGHT = "Copyright (c) 2025 Corey Haines"

#: The one `.agents/` file that is OMOS-authored. It must not be published and
#: must not be bundled; see the packet and docs/opensource/license-audit.md 8.1.
WITHHELD_AGENTS_FILE = ".agents/product-marketing.md"


# --- helpers -----------------------------------------------------------------

def _lf(text: str) -> str:
    """CRLF-normalised text.

    The repository has no `.gitattributes` and `core.autocrlf` is `true` on the
    Windows dev machine, so a checkout of an LF file arrives as CRLF. Comparing
    raw bytes would make this gate pass on the machine that wrote the file and
    fail on the machine that cloned it, which is the worst possible property for
    a provenance gate.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _library_files() -> list[str]:
    """Every vendored library file, as repo-relative POSIX paths. Derived."""
    if not SKILLS_ROOT.is_dir():
        return []
    return sorted(
        p.relative_to(REPO_ROOT).as_posix() for p in SKILLS_ROOT.rglob("*") if p.is_file()
    )


def _skill_dirs() -> list[str]:
    """Every skill directory name, derived from the working tree."""
    if not SKILLS_ROOT.is_dir():
        return []
    return sorted(p.name for p in SKILLS_ROOT.iterdir() if p.is_dir())


@pytest.fixture(scope="module")
def exporter():
    spec = importlib.util.spec_from_file_location("export_public_repo", EXPORTER_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["export_public_repo"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def public_names(exporter) -> list[str]:
    """Every relative path the exporter would write, computed the same way the
    pre-existing DEV-008 export gate computes it (INCLUDE_ROOTS + INCLUDE_DIRS +
    INCLUDE_FILES + the docs allowlist)."""
    names: list[str] = []
    for root in exporter.INCLUDE_ROOTS:
        source = REPO_ROOT / root
        if not source.exists():
            continue
        for rel in exporter.plan(source):
            names.append(f"{root}/{rel.as_posix()}")
    for name in exporter.INCLUDE_DIRS:
        source = REPO_ROOT / name
        if source.is_dir():
            for rel in exporter.plan(source):
                names.append(f"{name}/{rel.as_posix()}")
    for name in exporter.INCLUDE_FILES:
        if (REPO_ROOT / name).is_file():
            names.append(name)
    docs_root = REPO_ROOT / "docs"
    if docs_root.is_dir():
        for candidate in docs_root.rglob("*"):
            if candidate.is_file():
                rel_doc = candidate.relative_to(REPO_ROOT).as_posix()
                if rel_doc in exporter.DOCS_ALLOWLIST:
                    names.append(rel_doc)
    return sorted(set(names))


# --- 1. the vendored licence is the upstream bytes ---------------------------

def test_upstream_licence_is_vendored_verbatim():
    """The file itself, not a paraphrase of it.

    MIT's condition is that the notice travels with the copies, so a rewritten
    or summarised licence is not compliance. The digest below is the upstream
    file at the pinned commit; the bytes on disk are re-hashed every run.
    """
    assert AGENTS_LICENSE.is_file(), (
        f"{AGENTS_LICENSE} is missing. The library may not be redistributed "
        "without the upstream licence travelling with it."
    )
    normalised = _lf(AGENTS_LICENSE.read_text(encoding="utf-8"))
    digest = hashlib.sha256(normalised.encode("utf-8")).hexdigest()
    assert digest == UPSTREAM_LICENSE_SHA256, (
        ".agents/LICENSE is not the upstream licence at the pinned commit. "
        f"Expected sha256 {UPSTREAM_LICENSE_SHA256}, measured {digest}. A "
        "re-pin must update this constant in the same change that updates the "
        "lockfile."
    )
    assert len(normalised.encode("utf-8")) == UPSTREAM_LICENSE_BYTES, (
        "the vendored licence changed length; the vendored copy is not verbatim"
    )
    assert normalised.startswith("MIT License"), "the vendored licence is not MIT"
    assert UPSTREAM_COPYRIGHT in normalised, (
        "MIT requires the upstream copyright notice to travel with the copy; "
        "the vendored licence does not carry it"
    )
    assert "Permission is hereby granted, free of charge" in normalised
    assert "WITHOUT WARRANTY OF ANY KIND" in normalised


def test_notice_records_the_upstream_grant():
    """`NOTICE` is where a downstream reader looks, and the audit is not."""
    notice = (REPO_ROOT / "NOTICE").read_text(encoding="utf-8")
    for required in (UPSTREAM_REPO, UPSTREAM_TAG, UPSTREAM_COMMIT, "MIT",
                     UPSTREAM_COPYRIGHT):
        assert required in notice, f"NOTICE does not record {required!r}"
    # The notice has to point at the file that actually travels with the copies.
    assert ".agents/LICENSE" in notice, (
        "NOTICE names the licence but not the vendored copy of it"
    )


# --- 2. the export decision --------------------------------------------------

def test_agents_is_no_longer_a_blanket_forbidden_root(exporter):
    """`.agents` left FORBIDDEN_ROOTS for a reason, so the reason is asserted."""
    assert ".agents" not in exporter.FORBIDDEN_ROOTS
    assert exporter.AGENTS_INCLUDE_ROOT in exporter.INCLUDE_ROOTS
    assert exporter.AGENTS_LICENSE_FILE in exporter.INCLUDE_FILES
    # A blanket include of the directory is exactly the mistake being prevented.
    assert ".agents" not in exporter.INCLUDE_ROOTS
    assert ".agents" not in exporter.INCLUDE_FILES


def test_export_ships_the_whole_library_and_its_licence(public_names):
    """Every vendored file, counted from the working tree, not from a constant."""
    library = _library_files()
    assert library, f"no vendored library found under {SKILLS_ROOT}"
    missing = [name for name in library if name not in set(public_names)]
    assert missing == [], f"vendored library files would not be published: {missing[:5]}"
    assert ".agents/LICENSE" in public_names
    shipped = [n for n in public_names if n.startswith(".agents/")]
    assert len(shipped) == len(library) + 1, (
        "the .agents/ payload is not exactly the library plus its licence: "
        f"{len(shipped)} published vs {len(library)} library files + 1 licence"
    )


def test_export_ships_every_skill_directory(public_names):
    """One directory per skill, each with its SKILL.md, all derived."""
    published = set(public_names)
    for slug in _skill_dirs():
        assert f".agents/skills/{slug}/SKILL.md" in published, (
            f"skill {slug!r} would not be published"
        )


def test_export_withholds_the_omos_authored_agents_file(exporter, public_names):
    assert WITHHELD_AGENTS_FILE in exporter.AGENTS_WITHHELD, (
        "the withheld file must be declared, not merely omitted, so the reason "
        "survives a later refactor"
    )
    assert WITHHELD_AGENTS_FILE not in public_names, (
        f"{WITHHELD_AGENTS_FILE} is OMOS-authored, not upstream, and must not be "
        "published under the upstream MIT grant"
    )
    # The discrimination matters: the upstream library has a skill directory of
    # the same base name, and it *does* ship. A check that only grepped for
    # "product-marketing" could not tell the two apart.
    assert ".agents/skills/product-marketing/SKILL.md" in public_names


# --- 3. the exporter fails closed on anything else under .agents/ ------------

@pytest.mark.parametrize(
    "name",
    [
        ".agents/product-marketing.md",
        ".agents/anything-else.md",
        ".agents/skills",
        ".agents/skills-backup/ab-testing/SKILL.md",
        ".agents",
    ],
)
def test_audit_payload_rejects_every_other_agents_path(exporter, name):
    problems = exporter.audit_payload([name])
    assert problems, f"audit_payload accepted a non-redistributable path: {name}"


def test_audit_payload_accepts_the_two_redistributable_shapes(exporter):
    clean = [
        ".agents/LICENSE",
        ".agents/skills/ab-testing/SKILL.md",
        ".agents/skills/ab-testing/evals/evals.json",
    ]
    assert exporter.audit_payload(clean) == []


def test_exporter_declares_no_skill_count(exporter):
    """Anti-hardcode guard.

    The count is a property of the pinned SHA and must be recomputed, never
    branched on. If a future edit introduces a count constant, the re-pin story
    silently becomes a code change.
    """
    count_like = [
        name
        for name in dir(exporter)
        if re.search(r"(SKILL|LIBRARY).*(COUNT|NUM|TOTAL|SIZE)$", name)
        or re.search(r"^(COUNT|TOTAL).*(SKILL|LIBRARY)", name)
    ]
    assert count_like == [], f"the exporter declares a count constant: {count_like}"


# --- 4. the Windows payload --------------------------------------------------

def _spec_datas(root: Path | None = None,
                runtime_dir: Path | None = None) -> list[tuple[str, str]]:
    """The `datas` list from `packaging/omos.spec`, read as data.

    Parsed, not imported: the spec needs PyInstaller to execute, and a test
    suite that requires the packaging toolchain to check a packaging list is a
    test suite that skips itself. The single `datas` assignment is evaluated in
    an empty namespace with `os` bound, which is all it uses.
    """
    tree = ast.parse(SPEC_FILE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "datas" for t in node.targets
        ):
            namespace: dict[str, object] = {
                "os": os,
                # DEV-015: the frozen spec now collects Chroma data files.
                # This test only inspects the frontend/.agents destinations;
                # no PyInstaller collection is needed here.
                "collect_data_files": lambda _package: [],
                # `ROOT` is `os.path.abspath(os.getcwd())` in the spec, and the
                # build script runs PyInstaller from the repository root.
                "ROOT": str(root or REPO_ROOT),
                "RUNTIME_DIR": str(runtime_dir or (root or REPO_ROOT) / "runtime"),
            }

            exec(  # noqa: S102 - a packaging list from our own repository
                compile(ast.Module(body=[node], type_ignores=[]), "<omos.spec>", "exec"),
                namespace,
            )
            return [(src, dest) for src, dest in namespace["datas"]]
    raise AssertionError("packaging/omos.spec has no module-level `datas` list")


def test_frontend_dist_override_is_canonical_and_repo_contained(tmp_path, monkeypatch):
    tree = ast.parse(SPEC_FILE.read_text(encoding="utf-8"))
    resolver_node = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "resolve_frontend_dist"
    )
    namespace: dict[str, object] = {"os": os, "re": re}
    exec(  # noqa: S102 - this is the local helper from our packaging spec
        compile(ast.Module(body=[resolver_node], type_ignores=[]), "<omos.spec resolver>", "exec"),
        namespace,
    )
    resolve = namespace["resolve_frontend_dist"]
    root = tmp_path / "repo"

    def make_bundle(path: Path):
        assets = path / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        (assets / "main-current.js").write_text("bundle", encoding="utf-8")
        (path / "index.html").write_text(
            '<script src="/assets/main-current.js"></script>', encoding="utf-8")

    default_dist = root / "frontend" / "dist"
    make_bundle(default_dist)
    monkeypatch.delenv("OMOS_FRONTEND_DIST", raising=False)
    assert resolve(str(root)) == str(default_dist.resolve())
    monkeypatch.setenv("OMOS_FRONTEND_DIST", "   ")
    frontend_sources = [
        src for src, dest in _spec_datas(root, tmp_path / "runtime")
        if dest == os.path.join("frontend", "dist")
    ]
    assert frontend_sources == [str(default_dist.resolve())]

    isolated = root / "spa"
    make_bundle(isolated)
    monkeypatch.setenv("OMOS_FRONTEND_DIST", os.path.relpath(isolated, root))
    assert resolve(str(root)) == str(isolated.resolve())

    monkeypatch.setenv("OMOS_FRONTEND_DIST", "../frontend-outside-repo")
    with pytest.raises(ValueError, match="inside the repository root"):
        resolve(str(root))


def test_spec_parses_and_bundles_the_narrow_agents_payload():
    assert SPEC_FILE.is_file()
    datas = _spec_datas()
    sources = [src.replace("\\", "/") for src, _ in datas]
    assert any(src.endswith("/.agents/skills") for src in sources), (
        "the frozen app cannot read the skills library off disk without it"
    )
    assert any(src.endswith("/.agents/LICENSE") for src in sources), (
        "shipping MIT-licensed files without their notice is a redistribution "
        "defect in the installer specifically"
    )
    # The whole-directory include is the bug this narrows: it would carry
    # .agents/product-marketing.md into every user's install.
    assert not any(re.fullmatch(r".*/\.agents", src) for src in sources), (
        "packaging/omos.spec bundles the whole .agents directory, which would "
        "ship the OMOS-authored product-marketing.md to every user"
    )


def test_installer_script_needs_no_change_and_still_recurses_the_payload():
    """`packaging/omos.iss:79` copies the payload recursively, so the spec is
    the only choke point for what the installer contains."""
    text = ISS_FILE.read_text(encoding="utf-8", errors="replace")
    payload_lines = [
        line
        for line in text.splitlines()
        if "payload" in line.lower() and "source" in line.lower()
    ]
    assert payload_lines, "packaging/omos.iss no longer copies the build payload"
    assert any("recursesubdirs" in line.lower() for line in payload_lines), (
        "the installer no longer recurses the payload; a new per-file copy step "
        "would be required and this packet does not add one"
    )
    assert ".agents" not in text, (
        "packaging/omos.iss references .agents directly, so the spec is no "
        "longer the only choke point"
    )


# --- 5. the documents that carried the wrong claim ---------------------------

def test_license_audit_no_longer_records_the_library_as_unresolved():
    text = (REPO_ROOT / "docs" / "opensource" / "license-audit.md").read_text(
        encoding="utf-8"
    )
    # The standing rule at the top of the document still defines UNRESOLVED as a
    # category for *other* items; removing the word would delete that rule. What
    # must not survive is an unresolved verdict attached to the skills library.
    offenders = [
        line
        for line in text.splitlines()
        if "UNRESOLVED" in line and (".agents" in line or "skills library" in line)
    ]
    assert offenders == [], (
        "the licence audit still carries an unresolved verdict for the skills "
        f"library: {offenders}"
    )
    for required in (UPSTREAM_REPO, UPSTREAM_TAG, UPSTREAM_COMMIT, UPSTREAM_COPYRIGHT):
        assert required in text, f"the audit does not record {required!r}"
    # The withholding decision has to be documented, not just implemented, or
    # the next audit re-opens the question.
    assert "product-marketing.md" in text
    assert "not upstream" in text.lower()


def test_license_audit_records_the_licence_placeholder_decision():
    text = _lf((REPO_ROOT / "docs" / "opensource" / "license-audit.md").read_text(
        encoding="utf-8"
    ))
    assert "LICENSE:189" in text, (
        "the unfilled Apache appendix placeholder must have a recorded decision"
    )
    licence_text = _lf((REPO_ROOT / "LICENSE").read_text(encoding="utf-8"))
    assert "Copyright [yyyy] [name of copyright owner]" in licence_text, (
        "LICENSE is expected to be the verbatim Apache-2.0 text, including the "
        "appendix template. If it was changed, this test and the audit must be "
        "updated together."
    )
    # Attribution lives in NOTICE, which is where Apache-2.0 §4(d) puts it.
    assert "Copyright 2026 Open Marketing OS contributors" in (
        REPO_ROOT / "NOTICE"
    ).read_text(encoding="utf-8")


def test_gate_packet_no_longer_claims_sixty_skills():
    document = json.loads(
        (REPO_ROOT / "docs" / "marketing-skills-manifest.json").read_text(encoding="utf-8"))
    measured = len(_skill_dirs())
    assert document["skill_count"] == measured
    assert document["library_root"] == ".agents/skills"
    assert all(entry["path"].startswith(".agents/skills/")
               for entry in document["skills"])


# --- 6. the CLI behaves as documented ----------------------------------------

def test_dry_run_lists_the_narrow_include_and_the_withheld_file(exporter, tmp_path, capsys):
    out = tmp_path / "public"
    code = exporter.main(
        ["--out", str(out), "--dry-run", "--allow-placeholder-url"]
    )
    captured = capsys.readouterr()
    assert code == 0, f"the dry run refused to export:\n{captured.err}"
    assert not out.exists(), "a dry run wrote to disk"

    assert re.search(r"^\s+\.agents\s+\d+ files$", captured.out, re.M), (
        "the dry run summary does not report the .agents/ include"
    )
    assert exporter.AGENTS_INCLUDE_ROOT in captured.out
    assert exporter.AGENTS_LICENSE_FILE in captured.out
    withheld_source = REPO_ROOT / WITHHELD_AGENTS_FILE
    if withheld_source.is_file():
        assert WITHHELD_AGENTS_FILE in captured.out
    else:
        assert WITHHELD_AGENTS_FILE not in captured.out

    reported = int(
        re.search(r"^\s+\.agents\s+(\d+) files$", captured.out, re.M).group(1)
    )
    assert reported == len(_library_files()) + 1, (
        f"the dry run claims {reported} .agents/ files; the tree holds "
        f"{len(_library_files())} library files plus the licence"
    )
