"""Release gate for provider-neutral graph source and frozen bytecode."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import CodeType


CAPABILITY = "instagram_public_profile"
FORBIDDEN = (
    "apify", "brightdata", "graph.facebook.com", "api.apify.com",
    "api.brightdata.com", "APIFY_API_TOKEN", "BRIGHTDATA_API_TOKEN",
    "META_IG_ACCESS_TOKEN", "access_token", "run-sync-get-dataset-items",
    "actor_id", "dataset_id", "vault://", "provider_chain",
    "provider_order", "primary_provider", "fallback_provider",
)
CAPABILITY_MODULES = {
    "app.graphs.account_manager",
    "app.graphs.account_manager_graph",
    "app.graphs.employee_tools",
    "app.graphs.tool_capability",
}


def required_archive_modules() -> set[str]:
    return set(CAPABILITY_MODULES)


def graph_sources(graphs_dir: Path) -> dict[str, Path]:
    sources = {}
    for path in sorted(graphs_dir.rglob("*.py")):
        relative = path.relative_to(graphs_dir).with_suffix("")
        parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
        module = "app.graphs" + ("." + ".".join(parts) if parts else "")
        sources[module] = path
    return sources


def scan_source(graphs_dir: Path) -> list[str]:
    problems: list[str] = []
    sources = graph_sources(graphs_dir)
    if not sources:
        return ["no app/graphs Python source files found"]
    for module in sorted(CAPABILITY_MODULES):
        if module not in sources:
            problems.append(f"required capability source module missing: {module}")
        elif CAPABILITY not in sources[module].read_text(encoding="utf-8"):
            problems.append(f"{module} does not reference capability {CAPABILITY}")
    for module, path in sources.items():
        text = path.read_text(encoding="utf-8")
        lowered = text.casefold()
        for token in FORBIDDEN:
            if token.casefold() in lowered:
                problems.append(f"{module} contains forbidden provider detail: {token}")
    return problems


def nested_constants(code: CodeType):
    """Yield constants recursively, including constants in nested functions."""
    for value in code.co_consts:
        yield value
        if isinstance(value, CodeType):
            yield from nested_constants(value)


def _contains_forbidden_constant(code: CodeType) -> list[str]:
    found: set[str] = set()
    for value in nested_constants(code):
        if isinstance(value, str):
            for token in FORBIDDEN:
                if token.casefold() in value.casefold():
                    found.add(token)
    return sorted(found, key=str.casefold)


def scan_archive(exe: Path, expected_modules: set[str] | None = None) -> list[str]:
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError as exc:
        return [f"selected interpreter cannot import PyInstaller archive reader: {type(exc).__name__}"]
    try:
        archive = CArchiveReader(str(exe))
        pyz_names = [name for name, entry in archive.toc.items() if entry[-1] == "z"]
        if not pyz_names:
            return ["frozen executable has no embedded PYZ archive"]
        pyz = archive.open_embedded_archive(pyz_names[0])
    except Exception as exc:
        return [f"could not inspect frozen PYZ archive: {type(exc).__name__}"]

    module_names = set(pyz.toc)
    required = expected_modules if expected_modules is not None else required_archive_modules()
    problems = [f"frozen graph module missing: {name}" for name in sorted(required - module_names)]
    graph_names = sorted(name for name in module_names if name.startswith("app.graphs."))
    if not graph_names:
        problems.append("frozen PYZ contains no app.graphs modules")
    capability_found = False
    for name in graph_names:
        try:
            code = pyz.extract(name)
        except Exception as exc:
            problems.append(f"could not read frozen graph module {name}: {type(exc).__name__}")
            continue
        if not isinstance(code, CodeType):
            continue
        for value in nested_constants(code):
            if value == CAPABILITY:
                capability_found = True
                break
        for token in _contains_forbidden_constant(code):
            problems.append(f"frozen graph module {name} contains forbidden provider detail: {token}")
    if not capability_found:
        problems.append(f"frozen graph bytecode does not contain capability {CAPABILITY}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graphs-dir", type=Path, required=True)
    parser.add_argument("--exe", type=Path, required=True)
    args = parser.parse_args(argv)
    failures = scan_source(args.graphs_dir)
    failures.extend(scan_archive(args.exe, required_archive_modules()))
    if failures:
        print("PROVIDER_BOUNDARY_FAILED")
        for problem in failures:
            print(f" - {problem}")
        return 1
    print("PROVIDER_BOUNDARY_OK: graph source and frozen PYZ are provider-neutral")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
