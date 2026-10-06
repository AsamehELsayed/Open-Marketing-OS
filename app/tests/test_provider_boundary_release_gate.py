from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "provider_boundary_gate", ROOT / "scripts" / "package" / "verify_provider_boundary.py"
)
gate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(gate)


def _write_graph_tree(root: Path, *, capability: bool = True, forbidden: str = "") -> None:
    for module in gate.CAPABILITY_MODULES:
        path = root / (module.removeprefix("app.graphs.") + ".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            ('CAP = "instagram_public_profile"\n' if capability else "CAP = 'other'\n")
            + (f"DETAIL = {forbidden!r}\n" if forbidden else ""),
            encoding="utf-8",
        )
    (root / "misc.py").write_text("VALUE = 1\n", encoding="utf-8")


def test_source_scan_accepts_capability_only_graphs(tmp_path):
    _write_graph_tree(tmp_path)
    assert gate.scan_source(tmp_path) == []


def test_source_scan_rejects_missing_capability_and_vendor_detail(tmp_path):
    _write_graph_tree(tmp_path, capability=False, forbidden="APIFY_API_TOKEN")
    problems = gate.scan_source(tmp_path)
    assert any("does not reference capability" in item for item in problems)
    assert any("forbidden provider detail: APIFY_API_TOKEN" in item for item in problems)


def test_nested_code_constant_scan_finds_vendor_name():
    code = compile("def nested():\n    return 'brightdata'\n", "fixture.py", "exec")
    assert gate._contains_forbidden_constant(code) == ["brightdata"]


def test_archive_requires_capability_modules_only():
    assert gate.required_archive_modules() == gate.CAPABILITY_MODULES
    assert "app.graphs.misc" not in gate.required_archive_modules()
