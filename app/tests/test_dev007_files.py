"""DEV-007 W2 — Files pipeline acceptance tests.

Covers: upload matrix (pdf/docx/txt/md/csv/images), MIME mismatch,
oversize (monkeypatched caps), traversal names, missing project_id,
executable payload/extension, macro stripping (bytes preserved),
attach_scope turn vs project distinction, cross-project isolation canary,
image dims, no-filesystem-paths in responses, local ToolRecord fake,
w2.sql <-> repo drift guard.
"""
import io
import struct
import zlib
import zipfile
from pathlib import Path
from app.tests.internal_fixtures import requires_internal_runs

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import deps
from app.services.files import IngestError
from app.services.files.ingest import ingest as ingest_fn
from app.services.files import limits as files_limits
from app.services.files import repo as files_repo
from app.services.files.ingest import (
    ATTACH_SCOPES, FileRecord, tool_specs,
)
from app.services.files.chunk_index import chat_file_summary
from app.routes.files import router as files_router

REPO_ROOT = Path(__file__).resolve().parents[2]
W2_SQL_PATH = (
    REPO_ROOT / "development" / "runs" / "DEV-007" / "migrations" / "w2.sql"
)

PDF_BYTES = (
    b"%PDF-1.4\n1 0 obj\n<< /Length 44 >>\nstream\n"
    b"BT /F1 12 Tf 100 700 Td (Hello Acme Test Company PDF) Tj ET\n"
    b"endstream\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
)

TXT_BYTES = b"plain text document for W2 pipeline"
MD_BYTES = b"# Title\n\nproject scoped markdown body\n"
CSV_BYTES = b"a,b,c\n1,2,3\n"
DOCX_MACRO = b"FAKE-VBA-MACRO-BYTES-NOT-EXECUTABLE"


def make_png(width: int = 4, height: int = 3) -> bytes:
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" * (height * (1 + width * 3)))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )


def make_docx(text: str = "Hello docx", macro: bool = False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types/>',
        )
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
            f"{text}</w:t></w:r></w:p></w:body></w:document>",
        )
        if macro:
            zf.writestr("word/vbaProject.bin", DOCX_MACRO)
    return buf.getvalue()


def make_pdf(text: str = "Hello Acme Test Company PDF") -> bytes:
    stream = f"BT /F1 12 Tf 100 700 Td ({text}) Tj ET".encode("latin-1")
    return (
        b"%PDF-1.4\n1 0 obj\n<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
    )


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Isolated DB + storage root + bare app exposing only files.router."""
    db = tmp_path / "test.db"
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setattr(deps, "ROOT", root)
    deps.init_db(db)

    app = FastAPI()
    app.include_router(files_router)
    client = TestClient(app)

    from app.database import repos as db_repos
    with deps.get_db() as conn:
        for pid in ("projA", "projB"):
            db_repos.Projects.upsert(conn, {
                "id": pid, "name": pid, "website": "", "goal": "",
                "status": "active", "settings_json": "{}",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            })
    return {"client": client, "root": root, "db": db, "tmp": tmp_path}


def _upload(env, data: bytes, filename: str, project_id: str = "projA",
            attach_scope: str = "project", content_type: str | None = None,
            endpoint: str = "/files/upload", conversation_id: str | None = None):
    fields = {"project_id": project_id, "attach_scope": attach_scope}
    if conversation_id:
        fields["conversation_id"] = conversation_id
    return env["client"].post(
        endpoint,
        files={"file": (filename, data, content_type)},
        data=fields,
    )


# ---------- envelope + happy path ----------

def test_upload_txt_ok_returns_i5_record(env):
    r = _upload(env, TXT_BYTES, "notes.txt")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["ok"] is True
    data = body["data"]
    for key in ("file_id", "project_id", "original_name", "mime_detected",
                "size", "sha256", "kind", "width", "height", "extraction",
                "attach_scope", "indexed"):
        assert key in data, key
    assert data["project_id"] == "projA"
    assert data["mime_detected"] == "text/plain"
    assert data["kind"] == "document"
    assert data["extraction"] == "ready"
    assert data["attach_scope"] == "project"
    assert data["indexed"] is True
    assert data["size"] == len(TXT_BYTES)
    assert len(data["sha256"]) == 64
    # never expose disk layout
    assert "safe_name" not in data and "rel_path" not in data
    blob = str(body)
    assert "data/projects" not in blob and str(env["root"]) not in blob


def test_upload_matrix_document_kinds(env):
    cases = [
        (make_pdf(), "doc.pdf", "application/pdf", "ready"),
        (MD_BYTES, "readme.md", "text/markdown", "ready"),
        (CSV_BYTES, "table.csv", "text/csv", "ready"),
        (make_docx("docx body text"), "file.docx", None, "ready"),
    ]
    for data, name, ctype, status in cases:
        r = _upload(env, data, name, content_type=ctype)
        assert r.status_code == 201, (name, r.text)
        d = r.json()["data"]
        assert d["extraction"] == status, name
        assert d["indexed"] is True, name
        assert d["kind"] == "document", name


def test_upload_image_dims_and_not_indexed(env):
    png = make_png(4, 3)
    r = _upload(env, png, "pixel.png", content_type="image/png",
                endpoint="/files/upload/image")
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert d["kind"] == "image"
    assert d["mime_detected"] == "image/png"
    assert d["width"] == 4 and d["height"] == 3
    assert d["extraction"] == "ready"
    assert d["indexed"] is False  # images are stored, never FTS-indexed


def test_image_endpoint_rejects_documents(env):
    r = _upload(env, TXT_BYTES, "notes.txt", endpoint="/files/upload/image")
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "not_an_image"


# ---------- validation gates ----------

def test_missing_project_id_fails_closed(env):
    r = env["client"].post(
        "/files/upload",
        files={"file": ("a.txt", TXT_BYTES, "text/plain")},
        data={"project_id": "", "attach_scope": "project"},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "missing_project_id"

    r2 = env["client"].get("/files", params={"project_id": ""})
    assert r2.status_code == 400
    assert r2.json()["error"]["code"] == "missing_project_id"

    r3 = env["client"].get("/files/some-id", params={"project_id": ""})
    assert r3.status_code == 400


def test_unknown_project_rejected(env):
    # format-valid but not an existing project row -> fail-closed 404
    r = _upload(env, TXT_BYTES, "a.txt", project_id="nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "unknown_project"


def test_mime_mismatch_rejected(env):
    # PDF bytes declared as text/plain
    r = env["client"].post(
        "/files/upload",
        files={"file": ("x.pdf", PDF_BYTES, "text/plain")},
        data={"project_id": "projA", "attach_scope": "project"},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "mime_mismatch"


def test_traversal_filename_rejected(env):
    for bad in ("../../../etc/passwd", "..\\..\\win.ini",
                "/abs/path.txt", "C:\\windows\\system.ini",
                "C:/windows/system.ini", "a\x00b.txt"):
        r = _upload(env, TXT_BYTES, bad)
        if r.status_code == 201:
            # transport may normalize a dangerous name to a bare basename
            # (e.g. multipart strips C:\...); the stored name must be clean
            stored = r.json()["data"]["original_name"]
            assert "/" not in stored and "\\" not in stored
            assert ".." not in stored and ":" not in stored and "\x00" not in stored
        else:
            assert r.status_code == 400, bad
            assert r.json()["error"]["code"] == "unsafe_filename", bad
    # unit-level: validator itself rejects every raw dangerous form
    from app.services.files.safe_name import UnsafeFilename, reject_unsafe_filename
    for bad in ("../../../etc/passwd", "..\\..\\win.ini", "/abs/path.txt",
                "C:\\windows\\system.ini", "a\x00b.txt"):
        with pytest.raises(UnsafeFilename):
            reject_unsafe_filename(bad)


def test_executable_extension_rejected(env):
    r = _upload(env, b"MZ payload", "evil.exe")
    assert r.status_code == 400
    assert r.json()["error"]["code"] in (
        "executable_extension", "executable_payload")
    r2 = _upload(env, b"console.log(1)", "app.js")
    assert r2.status_code == 400
    assert r2.json()["error"]["code"] == "executable_extension"


def test_executable_magic_payload_rejected(env):
    # .txt extension but MZ magic
    r = _upload(env, b"MZ\x90\x00fake-pe", "sneaky.txt")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "executable_payload"


def test_oversize_rejected(env, monkeypatch):
    # Caps read dynamically from module attrs — monkeypatch applies live.
    monkeypatch.setattr(files_limits, "MAX_DOCUMENT_BYTES", 64)
    monkeypatch.setattr(files_limits, "MAX_IMAGE_BYTES", 64)
    r = _upload(env, b"A" * 65, "big.txt")
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "file_too_large"


def test_invalid_attach_scope_rejected(env):
    r = _upload(env, TXT_BYTES, "a.txt", attach_scope="forever")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_attach_scope"


def test_empty_file_rejected(env):
    r = _upload(env, b"", "empty.txt")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "empty_file"


def test_unsupported_binary_rejected(env):
    r = _upload(env, bytes(range(256)) * 4, "blob.bin",
                content_type="application/octet-stream")
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "unsupported_type"


# ---------- office macros ----------

def test_docx_macro_stripped_but_bytes_preserved(env):
    docx = make_docx("macro docx body", macro=True)
    r = _upload(env, docx, "macro.docm")
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert d["extraction"] == "stripped"
    # original bytes (incl. macro part) stored on disk untouched
    files = list((env["root"] / "data" / "projects" / "projA"
                  / "files").iterdir())
    assert len(files) == 1
    assert files[0].read_bytes() == docx
    # extracted text never contains the macro payload
    assert DOCX_MACRO.decode() not in str(d)
    # metadata row: macro part is ignored for text, not parsed
    with deps.get_db() as conn:
        row = conn.execute(
            "SELECT extraction FROM project_files WHERE file_id = ?",
            (d["file_id"],),
        ).fetchone()
    assert row["extraction"] == "stripped"


# ---------- attach_scope distinction ----------

def test_turn_scope_stores_but_never_indexes(env):
    from app.services import state
    with deps.get_db() as conn:
        conversation = state.create_conversation(conn, project_id="projA", title="Synthetic turn upload")
    r = _upload(env, b"turn-only secret memo xyzzy", "turn.txt",
                attach_scope="turn", conversation_id=conversation["id"])
    assert r.status_code == 201
    d = r.json()["data"]
    assert d["attach_scope"] == "turn"
    assert d["indexed"] is False
    assert d["extraction"] == "ready"  # text extracted, just not indexed
    # no documents/chunks/FTS rows
    with deps.get_db() as conn:
        docs = conn.execute(
            "SELECT COUNT(*) c FROM documents WHERE path LIKE ?",
            (f"project-files/projA/{d['file_id']}%",),
        ).fetchone()["c"]
        fts = conn.execute(
            "SELECT COUNT(*) c FROM chunks_fts WHERE text LIKE ?",
            ("%xyzzy%",),
        ).fetchone()["c"]
    assert docs == 0 and fts == 0
    # stored bytes still on disk — scoping governs indexing, not retention
    stored = list((env["root"] / "data" / "projects" / "projA"
                   / "files").iterdir())
    assert len(stored) == 1 and stored[0].read_bytes() == \
        b"turn-only secret memo xyzzy"


def test_project_scope_indexes_into_fts(env):
    r = _upload(env, b"unique-marker-quarkzoid project body", "p.txt",
                attach_scope="project")
    d = r.json()["data"]
    assert d["attach_scope"] == "project"
    assert d["indexed"] is True
    with deps.get_db() as conn:
        hit = conn.execute(
            "SELECT COUNT(*) c FROM chunks_fts WHERE text LIKE ?",
            ("%quarkzoid%",),
        ).fetchone()["c"]
    assert hit >= 1


def test_attach_scopes_constant():
    assert ATTACH_SCOPES == ("turn", "project")


# ---------- isolation canary ----------

def test_cross_project_isolation_canary(env, tmp_path):
    # Upload distinctive text into projA (project scope)
    marker = "xylophonquark isolation canary"
    r = _upload(env, marker.encode(), "canary.md", project_id="projA")
    assert r.status_code == 201
    file_id = r.json()["data"]["file_id"]

    # projB cannot see it (404 on direct read)
    r_b = env["client"].get(f"/files/{file_id}",
                            params={"project_id": "projB"})
    assert r_b.status_code == 404
    # projA can
    r_a = env["client"].get(f"/files/{file_id}",
                            params={"project_id": "projA"})
    assert r_a.status_code == 200

    # scoped retrieval: hits in projA, zero in projB
    from app.services.rag.scoped_retrieval import retrieve_scoped
    with deps.get_db() as conn:
        res_a = retrieve_scoped(conn, "xylophonquark",
                                project_id="projA", mode="lexical")
        res_b = retrieve_scoped(conn, "xylophonquark",
                                project_id="projB", mode="lexical")
    assert res_a["hits"], "projA should retrieve its uploaded file"
    assert all(h["project_id"] == "projA" for h in res_a["hits"])
    assert all("projB" not in h["path"] for h in res_a["hits"])
    assert not any("xylophonquark" in (h.get("text", "")
                                       + h.get("snippet", ""))
                   for h in res_b["hits"]), \
        "projB must never see projA uploaded content"
    # list endpoints are scoped too
    list_a = env["client"].get("/files", params={"project_id": "projA"})
    list_b = env["client"].get("/files", params={"project_id": "projB"})
    assert len(list_a.json()["data"]) >= 1
    assert all(f["file_id"] != file_id for f in list_b.json()["data"])


def test_chunks_rows_carry_explicit_project_id(env):
    r = _upload(env, b"project id propagation check body", "prop.txt")
    fid = r.json()["data"]["file_id"]
    with deps.get_db() as conn:
        rows = conn.execute(
            "SELECT c.project_id FROM chunks c"
            " JOIN documents d ON d.id = c.document_id"
            " WHERE d.path = ?",
            (f"project-files/projA/{fid}",),
        ).fetchall()
    assert rows, "expected chunk rows"
    assert all(rw["project_id"] == "projA" for rw in rows)


# ---------- chat-safe payload + ToolRecord shape ----------

def test_chat_file_summary_has_no_paths():
    row = {
        "file_id": "abc123", "project_id": "projA",
        "original_name": "x.txt", "safe_name": "deadbeef_x.txt",
        "mime_detected": "text/plain", "size": 10, "sha256": "f" * 64,
        "kind": "document", "width": None, "height": None,
        "extraction": "ready", "attach_scope": "turn", "indexed": 0,
        "rel_path": "data/projects/projA/files/deadbeef_x.txt",
    }
    out = chat_file_summary(row)
    assert out["file_id"] == "abc123"
    assert out["attach_scope"] == "turn"
    assert "rel_path" not in out and "safe_name" not in out
    assert not any(isinstance(v, str) and "data/projects" in v
                   for v in out.values())


def test_secret_hit_quarantines_index(env):
    # api_key pattern triggers rag.secrets deny-list -> quarantine
    r = _upload(env, b"intro\napi_key: supersecretvalue123\nmore",
                "secrets.txt")
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert d["indexed"] is False
    assert d["quarantine"], "expected quarantine kinds"
    # stored on disk but absent from FTS
    with deps.get_db() as conn:
        hit = conn.execute(
            "SELECT COUNT(*) c FROM chunks_fts WHERE path LIKE ?",
            (f"project-files/projA/{d['file_id']}%",),
        ).fetchone()["c"]
    assert hit == 0


def test_tool_specs_matches_toolrecord_shape():
    """Local value-object fake for the documented W4 ToolRecord fields —
    the real registry is owned by another worker (not imported here)."""
    class FakeToolRecord:
        def __init__(self, tool_id, source_type, side_effect,
                     credential_scope):
            self.tool_id = tool_id
            self.source_type = source_type
            self.side_effect = side_effect
            self.credential_scope = credential_scope

    specs = tool_specs()
    assert specs, "files tools must be declared"
    for spec in specs:
        rec = FakeToolRecord(**spec)
        assert rec.source_type in ("native", "integration", "mcp")
        assert rec.side_effect in ("green", "yellow", "red")
        assert rec.credential_scope in ("installation", "project", "user",
                                        "none", "")
        assert rec.tool_id.startswith("files_")


# ---------- repo / migration drift ----------

@requires_internal_runs
def test_w2_sql_matches_repo_drift_guard():
    assert W2_SQL_PATH.exists(), "w2.sql deliverable missing"
    file_sql = W2_SQL_PATH.read_text(encoding="utf-8")
    # strip SQL comments for comparison
    file_lines = [ln for ln in file_sql.splitlines()
                  if not ln.strip().startswith("--")]
    file_body = "\n".join(file_lines).strip()
    repo_body = files_repo.W2_SQL.strip()
    assert file_body == repo_body, (
        "w2.sql and repo.W2_SQL drifted — keep them byte-identical"
        " modulo comments")


def test_ensure_schema_idempotent(tmp_path):
    from app.database.sqlite import connect

    conn = connect(tmp_path / "s.db")
    files_repo.ensure_schema(conn)
    files_repo.ensure_schema(conn)  # second run must not raise
    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(project_files)").fetchall()}
    assert {"file_id", "project_id", "extraction", "attach_scope",
            "indexed", "rel_path"}.issubset(cols)
    conn.close()


def test_get_file_blank_project_id_fails_closed(tmp_path):
    from app.database.sqlite import connect

    conn = connect(tmp_path / "f.db")
    files_repo.ensure_schema(conn)
    with pytest.raises(ValueError):
        files_repo.get_file(conn, "x", "")
    with pytest.raises(ValueError):
        files_repo.get_file(conn, "x", None)
    with pytest.raises(ValueError):
        files_repo.list_files(conn, "   ")
    conn.close()


# ---------- FileRecord frozen shape ----------

def test_filerecord_is_frozen_i5_shape():
    rec = FileRecord(
        file_id="a" * 32, project_id="projA", original_name="n.txt",
        safe_name="s", mime_detected="text/plain", size=3,
        sha256="b" * 64, kind="document",
    )
    with pytest.raises(Exception):
        rec.file_id = "changed"  # frozen dataclass
    d = rec.as_dict()
    expected = {
        "file_id", "project_id", "original_name", "safe_name",
        "mime_detected", "size", "sha256", "kind", "width", "height",
        "extraction",
    }
    assert set(d.keys()) == expected


def test_ingest_rejects_via_ingesterror_codes(env):
    with pytest.raises(IngestError) as ei:
        ingest_fn(
            None, project_id="", filename="a.txt", data=b"x",
            root=env["root"],
        )
    assert ei.value.code == "missing_project_id"
    assert ei.value.status == 400
