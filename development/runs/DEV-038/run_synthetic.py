"""Offline Northstar Strategy Brief acceptance using production generation and storage."""
from __future__ import annotations

import hashlib
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app.database.sqlite import connect
from app.database import sqlite as sqlite_module
from app.services import campaign_production as production
from app.services.llm.model_router import ModelRouter
from app.services.llm import model_router as model_router_module
from app.routes.business_workspace import diagnose_unsupported_citations  # preload validator before network lock

PROJECT_ID = "6c4aaf8b966d43f1ae87b63707b3ed2c"
CAMPAIGN_ID = "9b9afc7746cb52ccaccac865ffa8025d"
SOURCE_DB = Path(r"C:\Users\User\Documents\open-marketing-os\development\acceptance\DEV-032-REAL-AI-E2E\free-cloud-20261011-attempt-02\workspace\data\marketing.db")
TEMPLATE_DB = Path(r"C:\Users\User\Documents\open-marketing-os\development\acceptance\OPENROUTER-STRICT-ROUTING-FIX-2026-10-11\real-attempt-01\workspace\data\marketing.db")
SOURCE_HASH = "f54a384efbbe789e0c7f2ca2eafa9563a4796e08fbd952cc7fe7f790f9eac507"
TEMPLATE_HASH = "7805082672e4fb69a22bb4e7f4a944951bc3a49e61a071d18ceae77f08940f19"
RUN = Path(__file__).resolve().parent / "synthetic-output"
APPROVED_BASE = "7136389aed293c152fb387de2a2f97bc1cd2dd47"
APPROVED_TREE = "88881ace7c42781e7db3d538d3bc84263d5c62b0"
REQUEST = "Create one seven-day Instagram strategy brief introducing Northstar Coffee to home brewers in Cairo using project-scoped evidence."


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("Source Git verification failed")
    return result.stdout.strip()


def verify_source() -> dict:
    """Allow only a clean descendant of the approved DEV-037 source on this branch."""
    if Path(git("rev-parse", "--show-toplevel")).resolve() != ROOT:
        raise RuntimeError("Source checkout root mismatch")
    if git("rev-parse", "--verify", f"{APPROVED_BASE}^{{tree}}") != APPROVED_TREE:
        raise RuntimeError("Approved DEV-037 base tree mismatch")
    head = git("rev-parse", "HEAD")
    if git("merge-base", "HEAD", APPROVED_BASE) != APPROVED_BASE:
        raise RuntimeError("Source is not a descendant of approved DEV-037")
    if git("branch", "--show-current") != "codex/dev-038-autonomous-e2e":
        raise RuntimeError("Unexpected acceptance branch")
    if git("status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("Source checkout is dirty")
    for module in (production, sqlite_module, model_router_module):
        Path(module.__file__).resolve().relative_to(ROOT)
    return {"source_commit": head, "source_tree": git("rev-parse", "HEAD^{tree}"),
            "approved_base": APPROVED_BASE, "clean": True}


def fingerprint(conn, project_id: str) -> str:
    """Hash all rows of every project-bearing table for an unrelated project."""
    data = []
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        if not name.replace('_', '').isalnum():
            continue
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')]
        if "project_id" in cols:
            rows = [tuple(r) for r in conn.execute(f'SELECT * FROM "{name}" WHERE project_id=? ORDER BY rowid', (project_id,))]
            data.append((name, rows))
    return hashlib.sha256(repr(data).encode()).hexdigest()


class ScriptedProvider:
    def __init__(self, content: str):
        self.content = content
        self.calls = 0
        self.model = "synthetic-local"

    def complete(self, *, system, messages, tools, opts=None):
        self.calls += 1
        return SimpleNamespace(text=self.content, usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})


def build_synthetic_response(evidence_id: str) -> str:
    """A useful scripted brief; recommendations are proposals, not business facts."""
    return (
        '## DELIVERABLE type="strategy_brief" title="SYNTHETIC Northstar seven-day Instagram strategy" platform="Instagram"\n\n'
        '## Persisted user facts\nBusiness name: Northstar Coffee.\n\n'
        f'## Retrieved evidence\nNorthstar House Blend is sold in 250 g bags for EGP 380 [{evidence_id}].\n\n'
        '## AI suggestions\n'
        '**Audience and positioning:** Propose home brewers in Cairo who want a clearer path from bean choice to a satisfying cup. Position the content as a practical, welcoming guide to choosing and preparing coffee at home; this is a proposed message, not an established brand claim.\n\n'
        '**Messaging pillars:** Suggest approachable brewing guidance, product discovery grounded in cited facts, and small rituals that invite audience participation. Keep a visible distinction between product information and creative ideas.\n\n'
        '**Day One — orientation:** Suggest a cover image that asks what a home brewer wants from a cup; invite responses in the caption.\n'
        '**Day Two — product focus:** Suggest a clean bag photograph with the cited House Blend fact as a source-backed caption, and a question about how followers brew.\n'
        '**Day Three — brew setup:** Suggest a short preparation sequence showing a simple at-home setup; frame every step as optional guidance.\n'
        '**Day Four — choice prompt:** Suggest a carousel that helps viewers describe their preferred cup without claiming undocumented tasting notes for any product.\n'
        '**Day Five — visual ritual:** Suggest a quiet morning brewing scene and a caption prompting followers to share their routine.\n'
        '**Day Six — question format:** Suggest a story question box about home brewing obstacles; use answers to shape later educational content.\n'
        '**Day Seven — recap:** Suggest a carousel of the week’s practical ideas and ask which topic deserves a deeper follow-up.\n\n'
        '**Visual guidance:** Use genuine product photography where available, readable type, consistent warm light, and close crops of hands, beans, and brewing tools. Check that any pictured packaging and labels match actual approved assets before publishing.\n\n'
        '**Proposed success measures:** Recommendation: track saves and clicks after launch. Also review qualitative replies and question-box themes. Compare content formats only after observations exist; do not treat a target or baseline as an established result.\n\n'
        '## Unknown or unsupported information\nPerformance baselines and results are unknown. The second product’s exact name, tasting notes, stock, and approved imagery need confirmation before they appear as facts.\n'
    )


def main() -> dict:
    source_state = verify_source()
    if sha(SOURCE_DB) != SOURCE_HASH or sha(TEMPLATE_DB) != TEMPLATE_HASH:
        raise RuntimeError("Approved input database hash mismatch")
    for db in (SOURCE_DB, TEMPLATE_DB):
        if Path(str(db) + "-wal").exists() and Path(str(db) + "-wal").stat().st_size:
            raise RuntimeError("Input database has uncheckpointed WAL")
    if RUN.exists():
        raise RuntimeError("Synthetic output exists; use a fresh run directory")
    RUN.mkdir()
    db = RUN / "marketing.db"
    shutil.copyfile(TEMPLATE_DB, db)
    original_socket = socket.socket

    def blocked_socket(*args, **kwargs):
        raise RuntimeError("Network dispatch blocked in synthetic acceptance")

    socket.socket = blocked_socket
    conn = None
    try:
        conn = connect(db)
        conn.row_factory = sqlite3.Row
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        project = conn.execute("SELECT name FROM projects WHERE id=?", (PROJECT_ID,)).fetchone()
        campaign = conn.execute("SELECT title FROM campaigns WHERE id=? AND project_id=?", (CAMPAIGN_ID, PROJECT_ID)).fetchone()
        assert project and project[0] == "Northstar Coffee" and campaign
        other = conn.execute("SELECT id FROM projects WHERE id<>? ORDER BY id LIMIT 1", (PROJECT_ID,)).fetchone()
        assert other
        other_before = fingerprint(conn, other[0])
        context = production.build_generation_context(conn, project_id=PROJECT_ID, campaign_title=campaign[0], user_request=REQUEST)
        assert context["project_id"] == PROJECT_ID and context["evidence"] and context["profile"] and context["brief_md"]
        evidence = context["evidence"][0]
        assert evidence["source_id"] in context["evidence_ids"]
        content = build_synthetic_response(evidence["source_id"])
        provider = ScriptedProvider(content)
        production._router = lambda: ModelRouter(local_provider=provider, default_local_model="synthetic-local")
        result = production.generate_batch(conn, project_id=PROJECT_ID, campaign_id=CAMPAIGN_ID,
            turn_id="dev038-synthetic", user_request=REQUEST, types=["strategy_brief"],
            campaign_title=campaign[0], provider="LOCAL", model_id="synthetic-local")
        if not result.get("ok"):
            raise RuntimeError(f'Synthetic generation failed: {result.get("failure_code")}: {result.get("error")}')
        assert provider.calls == 1 and len(result["created"]) == 1
        conn.commit()
        rows = production.list_deliverables(conn, project_id=PROJECT_ID, campaign_id=CAMPAIGN_ID)
        created = next(r for r in rows if r["id"] == result["created"][0])
        assert created["type"] == "strategy_brief" and "SYNTHETIC" in created["content_md"]
        assert fingerprint(conn, other[0]) == other_before
        export = RUN / "northstar-strategy-brief-SYNTHETIC.md"
        export.write_text(f'# {created["title"]}\n\n{created["content_md"]}\n', encoding="utf-8")
        assert created["content_md"] in export.read_text(encoding="utf-8")
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        report = {"status": "PASS", "label": "SYNTHETIC", **source_state, "source_db_sha256_before": SOURCE_HASH,
            "source_db_sha256_after": sha(SOURCE_DB), "workspace_db": str(db), "workspace_db_sha256": sha(db),
            "deliverable_id": created["id"], "campaign_id": CAMPAIGN_ID, "project_id": PROJECT_ID,
            "source_id": evidence["source_id"], "export": str(export), "export_sha256": sha(export),
            "other_project_unchanged": True, "fake_provider_calls": provider.calls,
            "real_provider_requests": 0, "real_inference_posts": 0, "network_requests": 0,
            "schema_user_version": conn.execute("PRAGMA user_version").fetchone()[0]}
        (RUN / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
    finally:
        if conn:
            conn.close()
        socket.socket = original_socket


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
