# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — Open Marketing OS (Windows x64, one folder, no console).

DEV-008. Run via `scripts/package/build_windows.ps1`, not directly, so the
hidden-import list and the data files stay in one place.

Why one-folder and not one-file
--------------------------------
A one-file build unpacks the whole bundle to a temp directory on every launch,
which roughly doubles cold start and breaks the DPAPI credential store's
"same install, same paths" expectations during upgrades. One-folder is what the
Inno Setup installer ships; see
`docs/distribution/windows-packaging-decision.md`.

Why console=False
-----------------
A normal user must never see a terminal. All launcher output goes to
`%LOCALAPPDATA%\\OpenMarketingOS\\logs\\omos-launcher.log` and user-facing
failures use a native message box.

Layout inside the bundle
------------------------
`app/routes/spa.py` resolves the React bundle as
`<parents of app/routes/spa.py>/frontend/dist`, and `app/main.py` resolves
templates and static as `<parents of app>/templates` and `.../static`. Under
PyInstaller `__file__` lives under `sys._MEIPASS`, so those three paths become
`_internal/frontend/dist`, `_internal/app/templates` and
`_internal/app/static`. The `datas` entries below match that exactly. A build
may set `OMOS_FRONTEND_DIST` to a repo-contained, prebuilt frontend directory;
the normal default remains `frontend/dist`.
"""
import os
import re

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

block_cipher = None

ROOT = os.path.abspath(os.getcwd())


def resolve_frontend_dist(root):
    """Resolve the prebuilt SPA, allowing an isolated validated bundle input.

    OMOS_FRONTEND_DIST may be absolute or relative to the repository root.
    Canonicalization and containment checks keep a typo/path traversal from
    pulling arbitrary host files into a release payload.
    """
    configured = (os.environ.get("OMOS_FRONTEND_DIST") or "").strip()
    candidate = configured or os.path.join("frontend", "dist")
    if not os.path.isabs(candidate):
        candidate = os.path.join(root, candidate)
    canonical_root = os.path.realpath(root)
    canonical = os.path.realpath(candidate)
    try:
        if os.path.commonpath([canonical_root, canonical]) != canonical_root:
            raise ValueError("OMOS_FRONTEND_DIST must resolve inside the repository root")
    except ValueError as exc:
        raise ValueError("OMOS_FRONTEND_DIST must resolve inside the repository root") from exc
    if not os.path.isdir(canonical):
        raise FileNotFoundError("Frontend bundle directory is missing: " + canonical)
    index_path = os.path.join(canonical, "index.html")
    if not os.path.isfile(index_path):
        raise FileNotFoundError("Frontend bundle index.html is missing: " + index_path)
    with open(index_path, "r", encoding="utf-8") as index_file:
        html = index_file.read()
    asset_root = os.path.realpath(os.path.join(canonical, "assets"))
    asset_refs = [ref.split("?", 1)[0] for ref in re.findall(
        r'''(?:src|href)=["']([^"']+)["']''', html
    ) if ref.startswith("/assets/")]
    js_refs = [ref for ref in asset_refs if ref.lower().endswith(".js")]
    if not js_refs:
        raise ValueError("Frontend index must reference a built JavaScript asset")
    for ref in asset_refs:
        asset = os.path.realpath(os.path.join(canonical, ref.lstrip("/\\")))
        try:
            under_assets = os.path.commonpath([asset_root, asset]) == asset_root
        except ValueError:
            under_assets = False
        if not under_assets or not os.path.isfile(asset):
            raise FileNotFoundError("Frontend bundle asset is missing or outside assets: " + ref)
    return canonical


FRONTEND_DIST = resolve_frontend_dist(ROOT)


def resolve_runtime_dir(root):
    """Resolve staged, hash-verified llama.cpp files for the DEV-029 payload."""
    configured = (os.environ.get("OMOS_RUNTIME_DIR") or "").strip()
    if not configured:
        raise RuntimeError("OMOS_RUNTIME_DIR must point to the verified staged runtime")
    candidate = configured if os.path.isabs(configured) else os.path.join(root, configured)
    canonical = os.path.realpath(candidate)
    canonical_root = os.path.realpath(root)
    try:
        if os.path.commonpath([canonical_root, canonical]) != canonical_root:
            raise ValueError("OMOS_RUNTIME_DIR must resolve inside the repository root")
    except ValueError as exc:
        raise ValueError("OMOS_RUNTIME_DIR must resolve inside the repository root") from exc
    if not os.path.isdir(canonical):
        raise FileNotFoundError("Staged Local runtime directory is missing: " + canonical)
    server = os.path.join(canonical, "llama-server.exe")
    if not os.path.isfile(server):
        raise FileNotFoundError("Staged Local runtime is missing llama-server.exe")
    notice_dir = os.path.join(canonical, "licenses", "llama.cpp")
    notice_names = {name.upper() for name in os.listdir(notice_dir)} if os.path.isdir(notice_dir) else set()
    required_notices = {"LICENSE", "LICENSE-LLVM-OPENMP"}
    if not required_notices.issubset(notice_names):
        raise FileNotFoundError("Staged Local runtime requires llama.cpp MIT and LLVM OpenMP notices")
    if any(name.lower().endswith(".gguf") for _base, _dirs, files in os.walk(canonical)
           for name in files):
        raise ValueError("Model weights must not be included in the installer")
    return canonical


RUNTIME_DIR = resolve_runtime_dir(ROOT)

# --- runtime imports PyInstaller's static analysis cannot see ---------------
# uvicorn resolves its loop/impl classes dynamically; langgraph and the OpenAI
# client do the same. Without these the frozen app fails at import time with a
# ModuleNotFoundError that only appears after installation.
HIDDEN_IMPORTS = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "langgraph.graph",
    "langgraph.checkpoint.sqlite",
    "langgraph.checkpoint.memory",
    "langchain_core.documents",
    "langchain_core.embeddings",
    "openai",
    "anyio._backends._asyncio",
    "onnxruntime",
    "tokenizers",
]
# DEV-008: production subpackages only. `collect_submodules("app")` also pulled
# in `app.tests`, which embeds synthetic secret-shaped fixtures (deliberate
# leak-detection sentinels) inside the shipped executable. The payload secret
# scan reads files on disk and cannot see inside the PYZ archive, so that was an
# unpoliceable blind spot in the "no secrets in the payload" claim.
#
# DEV-008-PUBLISH-GATE: an earlier version of this comment reproduced one of
# those sentinels verbatim as illustration. It was AWS's published example key,
# which is harmless in itself, but a realistic-looking credential sitting in
# shipped build configuration is exactly the thing every secret scanner flags,
# and a scanner that cries wolf gets switched off. The sentinels are described
# here instead, and each is marked at its definition site.
HIDDEN_IMPORTS += [
    module for module in collect_submodules("app")
    if not module.startswith("app.tests")
]
HIDDEN_IMPORTS += [
    module for module in collect_submodules("chromadb")
    if not module.startswith("chromadb.test")
]

# Never bundle these: dev-only, platform-irrelevant, or enormous. Local Chroma
# and CPU ONNX are part of the supported hybrid-capable runtime; model weights
# remain in the user's writable cache and are never bundled here.
EXCLUDES = [
    "torch",
    "torchvision",
    "playwright",
    "pytest",
    "_pytest",
    "IPython",
    "jupyter",
    "notebook",
    "tkinter",
    "matplotlib",
    "scipy",
    "sklearn",
    "transformers",
    "sentence_transformers",
    "PIL",
    "yaml.cython",
]

# --- data files -------------------------------------------------------------
datas = [
    *collect_data_files("chromadb"),
    # Prebuilt React bundle. `npm run build` must have run first; the build
    # script enforces that rather than shipping a missing UI.
    # Keep the historic frontend/dist default; packaging acceptance and other
    # isolated builds can point at a generated bundle without mutating it.
    (os.path.realpath(os.path.join(
        ROOT, os.environ.get("OMOS_FRONTEND_DIST", "").strip() or os.path.join("frontend", "dist")
    )), os.path.join("frontend", "dist")),
    # Jinja templates and legacy static assets, resolved relative to app/.
    (os.path.join(ROOT, "app", "templates"), os.path.join("app", "templates")),
    (os.path.join(ROOT, "app", "static"), os.path.join("app", "static")),
    # app/database/schema.sql is read at runtime by sqlite.connect() and applied
    # with executescript on every connection. PyInstaller's static analysis does
    # not see an `open()` of a sibling .sql file, so without this the frozen app
    # dies on startup with FileNotFoundError before serving a single request.
    # Caught by the build script's smoke test, not by any unit test.
    #
    # The destination is the *directory*. PyInstaller treats a `datas`
    # destination as a directory to place the source inside; giving it
    # "app/database/schema.sql" as the destination silently creates a directory
    # of that name and the read then fails with PermissionError.
    (os.path.join(ROOT, "app", "database", "schema.sql"),
     os.path.join("app", "database")),

    # config/*.yaml — the SECOND instance of the same bug class, found by the
    # independent review after the first one was fixed.
    #
    # `app/services/llm/model_router.py` resolves
    # `parents[3]/config/model_pricing.yaml` and `load_pricing()` calls
    # `read_text()` on it with no guard. ModelRouter is constructed on every
    # graph turn, so without these files the frozen app raised FileNotFoundError
    # on the first chat message. The failure was invisible: `_get_graph()`
    # converts any exception into "Graph runtime is unavailable", so the app
    # looked healthy and simply never answered.
    #
    # Bundled wholesale rather than file-by-file, because `v1_runtime.yaml`,
    # `routing.yaml`, `permissions.yaml` and `cost-policy.yaml` are the same
    # kind of read-at-runtime product configuration and will be next otherwise.
    (os.path.join(ROOT, "config"), "config"),

    # DEV-008-SKILLS-OPS: the pinned upstream marketing skills library and the
    # upstream MIT licence that must travel with it.
    #
    # The library is read at runtime, off disk, like `config/`: the skill
    # registry resolves `.agents/skills` relative to the same repository root
    # that `config/model_pricing.yaml` and `frontend/dist` already resolve
    # against, which under PyInstaller is `sys._MEIPASS` (`_internal/`). If that
    # convention ever differs, this is the third instance of the same bug class
    # described above -- a missing data file that only fails at runtime, in a
    # path whose exceptions are swallowed into "runtime unavailable".
    #
    # Two entries, NOT the `.agents` directory. `.agents/product-marketing.md`
    # is OMOS-authored and holds a named third party's Marketing Brain facts;
    # bundling the directory would ship it to every user. The narrowness is the
    # point, and `scripts/package/export_public_repo.py` applies the identical
    # rule to the public repository (AGENTS_INCLUDE_ROOT / AGENTS_LICENSE_FILE).
    #
    # `.agents/LICENSE` is bundled rather than left in the source tree alone
    # because PyInstaller does not copy the repository root: shipping 279 MIT
    # files without the notice they require would be a redistribution defect in
    # the installer specifically.
    (os.path.join(ROOT, ".agents", "skills"), os.path.join(".agents", "skills")),
    (os.path.join(ROOT, ".agents", "LICENSE"), os.path.join(".agents")),
    # llama.cpp's MIT notice accompanies the verified runtime binaries.
    (os.path.join(RUNTIME_DIR, "licenses", "llama.cpp"),
     os.path.join("runtime", "licenses", "llama.cpp")),
]

runtime_binaries = [
    (os.path.join(RUNTIME_DIR, name), "runtime")
    for name in sorted(os.listdir(RUNTIME_DIR))
    if name.lower().endswith((".exe", ".dll"))
]

analysis = Analysis(
    [os.path.join(ROOT, "launcher", "omos_launcher.py")],
    pathex=[ROOT],
    binaries=(collect_dynamic_libs("chromadb") + collect_dynamic_libs("onnxruntime")
              + runtime_binaries),
    datas=datas,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(analysis.pure, analysis.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="OpenMarketingOS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # No console window. This is the difference between "double-click an app"
    # and "double-click an app that flashes a black terminal".
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "packaging", "omos.ico")
    if os.path.isfile(os.path.join(ROOT, "packaging", "omos.ico")) else None,
)

coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.zipfiles,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="OpenMarketingOS",
)
