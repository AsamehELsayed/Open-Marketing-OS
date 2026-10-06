"""Product import-graph proof (DEV-007 W1): no OpenCode symbols from app runtime paths."""
import importlib
import pathlib
import re
import sys

import pytest

import app


APP_ROOT = pathlib.Path(app.__file__).resolve().parent
# Product runtime modules only (not tests; not historical fixtures).
_RUNTIME_EXCLUDE_PARTS = {"tests", "static", "templates"}

_FORBIDDEN_IMPORTS = re.compile(
    r"opencode_service|opencode_provider|OpenCodeProvider|OpenCodeUnavailable"
    r"|opencode_runtime_available|opencode_contract",
    re.I,
)


def _runtime_py_files():
    for p in sorted(APP_ROOT.rglob("*.py")):
        if _RUNTIME_EXCLUDE_PARTS.intersection(p.parts):
            continue
        yield p


def test_no_opencode_references_in_app_runtime_modules():
    offenders = []
    for path in _runtime_py_files():
        text = path.read_text(encoding="utf-8")
        if _FORBIDDEN_IMPORTS.search(text):
            offenders.append(str(path.relative_to(APP_ROOT.parent)))
    assert not offenders, f"opencode symbols still referenced: {offenders}"


def test_opencode_provider_module_not_importable():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.services.llm.opencode_provider")


def test_opencode_service_module_not_importable():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.services.opencode_service")


def test_llm_package_exports_have_no_opencode():
    import app.services.llm as llm
    for name in getattr(llm, "__all__", []):
        assert "opencode" not in name.lower()
    assert not any("opencode" in s.lower() for s in sys.modules if s.startswith("app.services.llm.opencode"))


def test_router_valid_set_has_no_opencode():
    from app.services.llm import router
    assert "opencode" not in router._VALID
    assert not hasattr(router, "opencode_runtime_available")
    cands = router.resolve_candidates("auto", ("openai", "deterministic"), openai_ok=False)
    assert cands == ["deterministic"]
    cands2 = router.resolve_candidates("auto", ("openai", "deterministic"), openai_ok=True)
    assert cands2[0] == "openai" and "opencode" not in cands2
