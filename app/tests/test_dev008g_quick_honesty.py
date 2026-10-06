"""DEV-008-PUBLISH-GATE / Gate 2 -- the public build must not claim what does not exist.

Beta 1 ships **OMOS Quick** and nothing else. There is no local-model edition,
no trained marketing adapter, and no downloadable weights. This module is the
falsifiable gate that keeps the public surface honest about that.

Why a test and not a review
---------------------------
The specific failure this prevents already happened once, in this repository.
`docs/releases/v1.0.0.md` described a fine-tuned marketing model as the primary
shipped intelligence, presented a 15-row "adoption evidence" table scoring it
100% against named real businesses, and declared the release FROZEN and
APPROVED. No such adapter was ever produced. The license audit caught it; a
release gate must, so every claim below is asserted against the real files
rather than trusted.

Two failure modes are covered:

1. **Claiming Local exists.** README, changelog, architecture docs, the Settings
   UI and the installer must say "not in this beta" or "coming in a later beta",
   never imply a working local model or offer a dead "Install Local" control.
2. **Claiming the LoRA shipped.** Nothing may assert that
   `OMOS-Qwen2.5-7B-Marketing-v1` is included, downloadable, or the active model.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Public-facing prose. Internal engineering docs are excluded on purpose: they
#: are allowed to discuss the local roadmap, they are just not user-facing.
PUBLIC_DOCS = (
    "README.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "NOTICE",
    "docs/architecture.md",
    "docs/privacy.md",
    "docs/troubleshooting.md",
    "docs/user-data-and-backup.md",
    "docs/distribution/windows-packaging-decision.md",
    "docs/opensource/license-audit.md",
    "docs/releases/v1.0.0.md",
)

#: The model that was claimed to ship and does not exist.
NONEXISTENT_ADAPTER = "omos-qwen2.5-7b-marketing-v1"

#: Frontend source files allowed to mention local AI. Settings is the one screen
#: that shows local state, and it must do so with a future-beta message.
FRONTEND_LOCAL_SURFACE = ("frontend/src/routes/Settings.tsx",)


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _flat(rel: str) -> str:
    """Text with runs of whitespace collapsed.

    Markdown hard-wraps prose, so a phrase asserted across a line break would
    otherwise fail for a formatting reason rather than a content one.
    """
    return re.sub(r"\s+", " ", _read(rel))


# --------------------------------------------------------------------------
# 1. No public document may claim the non-existent adapter shipped
# --------------------------------------------------------------------------

def test_no_public_document_claims_the_marketing_adapter_ships():
    """The exact claim that made the old v1.0.0 notes false.

    A correction is allowed: a document may *disclaim* the adapter, and the
    license audit must, because that is how a reader learns it does not exist.
    What is forbidden is a sentence presenting it as present, active, primary or
    downloadable.
    """
    claiming = re.compile(
        r"(includes?|bundled?|ships?|shipping|downloadable|available now|"
        r"primary marketing intelligence|is included|is available)"
        r"[^.]{0,80}" + re.escape(NONEXISTENT_ADAPTER),
        re.IGNORECASE,
    )
    offenders: dict[str, list[str]] = {}
    for rel in PUBLIC_DOCS:
        text = _read(rel)
        hits = [m.group(0)[:120] for m in claiming.finditer(text)]
        if hits:
            offenders[rel] = hits
    assert offenders == {}, f"documents claim a non-existent adapter ships: {offenders}"


def test_release_notes_record_the_correction():
    """A silent rewrite would hide that the claim was ever made."""
    flat = _flat("docs/releases/v1.0.0.md").lower()
    assert NONEXISTENT_ADAPTER in flat, (
        "the corrected release history must still name the adapter it retracts, "
        "or the correction is unauditable"
    )
    # The retraction has to be unambiguous, so require the substance rather than
    # one exact wording.
    for required in ("never released", "none of that was true", "does not exist"):
        assert required in flat, (
            f"the correction must state {required!r}; it does not"
        )


def test_license_audit_still_records_the_adapter_as_nonexistent():
    """The two documents must not disagree about reality."""
    text = _read("docs/opensource/license-audit.md").lower()
    assert NONEXISTENT_ADAPTER in text
    assert "does not exist" in text


# --------------------------------------------------------------------------
# 2. Local AI must be labelled unavailable, not implied available
# --------------------------------------------------------------------------

def test_readme_labels_quick_available_now_and_local_a_future_beta():
    readme = _read("README.md")
    assert "OMOS Quick" in readme
    quick_at = readme.index("OMOS Quick")
    local_at = readme.index("OMOS Local")
    assert quick_at < local_at, "Quick must be presented first; it is what ships"

    quick_block = readme[quick_at:local_at].lower()
    assert "available now" in quick_block, (
        "the shipping edition must say plainly that it is available now"
    )
    # The cloud providers the beta actually uses must be named up front.
    for provider in ("openrouter", "openai"):
        assert provider in quick_block, f"{provider} missing from the Quick section"

    local_block = readme[local_at:local_at + 1200].lower()
    assert "future beta" in local_block or "not part of this beta" in local_block
    assert "openrouter" in local_block or "openai" in local_block, (
        "the Local section should say what the beta uses instead"
    )


def test_readme_does_not_promise_a_local_model_or_weights():
    readme = _read("README.md").lower()
    for promise in (
        "run entirely on your machine with no api key",
        "no api key at all, available now",
        "download the model",
        "includes the model",
        "bundled model",
    ):
        assert promise not in readme, f"README promises {promise!r}"


def test_changelog_states_cloud_only_and_is_honest_about_signing():
    changelog = _read("CHANGELOG.md").lower()
    assert "no local-model edition in this beta" in changelog
    assert "unknown" in changelog and "publisher" in changelog, (
        "the changelog must state the installer is unsigned so SmartScreen's "
        "'Unknown publisher' warning is expected rather than alarming"
    )
    # And it must not tell users to weaken their machine.
    for harmful in (
        "disable windows defender",
        "turn off smartscreen",
        "disable smartscreen",
        "add an exception in windows security",
    ):
        assert harmful not in changelog, f"changelog advises {harmful!r}"


def test_architecture_doc_does_not_present_local_as_an_edition():
    flat = _flat("docs/architecture.md").lower()
    assert "cloud providers are the only path" in flat
    assert "do not document local models as an available edition" in flat


# --------------------------------------------------------------------------
# 3. The Settings UI must not offer a control that cannot work
# --------------------------------------------------------------------------

def test_settings_ui_marks_local_ai_as_a_later_beta():
    settings = _read("frontend/src/routes/Settings.tsx")
    assert "Coming in a later beta" in settings, (
        "the Local Intelligence section must be visibly labelled as not available "
        "in this beta"
    )
    assert "not available in this beta" in settings


def test_settings_ui_does_not_promise_local_execution():
    """The routing-mode copy used to promise execution that cannot happen.

    It told users `AUTO` "Executes locally by default" and offered a "Local Base
    Only — Purely local execution" choice. In a Quick-only beta neither is true,
    and offering them is precisely the "broken Local control" the release gate
    prohibits.
    """
    settings = _read("frontend/src/routes/Settings.tsx")
    for broken in (
        "Executes locally by default",
        "Purely local execution",
        "Local Base Only",
    ):
        assert broken not in settings, (
            f"Settings still offers {broken!r}, which cannot work in OMOS Quick"
        )


def test_no_frontend_offers_a_local_model_download():
    """No dead 'Install Local' button, no fake LoRA download."""
    forbidden = ("Install Local", "Download model", "Download LoRA",
                 "OMOS-Qwen2.5-7B-Marketing-v1")
    offenders: dict[str, list[str]] = {}
    for path in sorted((REPO_ROOT / "frontend" / "src").rglob("*.ts*")):
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = [f for f in forbidden if f in text]
        if hits:
            offenders[path.relative_to(REPO_ROOT).as_posix()] = hits
    assert offenders == {}, f"frontend offers a non-functional local control: {offenders}"


# --------------------------------------------------------------------------
# 4. The installer must not advertise a local edition
# --------------------------------------------------------------------------

def test_installer_ships_quick_only():
    iss = _read("packaging/omos.iss")
    lowered = iss.lower()
    # No task, component or file may offer a local model payload.
    for forbidden in ("qwen", ".gguf", "lora", "localmodel", "local-model"):
        assert forbidden not in lowered, (
            f"packaging/omos.iss references {forbidden!r}; beta 1 is Quick only"
        )
    assert "openmarketingos" in lowered


def test_release_workflow_builds_quick_only():
    workflow = _read(".github/workflows/release.yml")
    lowered = workflow.lower()
    assert "omos local" not in lowered, (
        "the public release workflow must not build a Local installer for beta 1"
    )
    assert "quick" in lowered
    assert "sha256" in lowered or "sha256sums" in lowered


# --------------------------------------------------------------------------
# 5. The whole tree must not *ship* model weights
# --------------------------------------------------------------------------

def test_no_model_weights_are_present_to_bundle():
    """Nothing that a packaging step could mistake for a redistributable weight.

    `.pth` is excluded deliberately: it is ambiguous between a PyTorch checkpoint
    and a Python path-configuration file, and `build/venv` legitimately contains
    `distutils-precedence.pth`. Treating that as a 0.5 GB model would make this
    gate cry wolf, and a gate that cries wolf gets ignored. A real weight file is
    megabytes large, so a size floor separates the two honestly.
    """
    weight_suffixes = (".gguf", ".safetensors", ".onnx", ".pt", ".ckpt")
    ignored_dirs = {".git", ".venv", "venv", "node_modules", "__pycache__",
                    ".pytest_cache", "worktrees"}
    min_bytes = 1_000_000
    found = [
        p.relative_to(REPO_ROOT).as_posix()
        for p in REPO_ROOT.rglob("*")
        if p.is_file()
        and p.suffix.lower() in weight_suffixes
        and not (ignored_dirs & set(p.parts))
        and p.stat().st_size >= min_bytes
    ]
    assert found == [], f"model weight files present: {found}"
