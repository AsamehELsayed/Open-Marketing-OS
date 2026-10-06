"""Conversation scoped attachment validation and evidence assembly."""
from pathlib import Path

from app.services.files import extract, hashing, repo, sniff
from app.services.rag.secrets import find_secrets

MAX_ATTACHMENT_IDS = 10


def validate_attachment_ids(value):
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or len(value) > MAX_ATTACHMENT_IDS:
        raise ValueError("attachment_ids must contain at most 10 file IDs")
    ids = [str(item).strip() for item in value]
    if any(not item or len(item) > 128 for item in ids) or len(set(ids)) != len(ids):
        raise ValueError("attachment_ids contains invalid or duplicate IDs")
    return ids


def assemble_attachment_evidence(conn, *, root, project_id, conversation_id, file_ids):
    ids = validate_attachment_ids(file_ids)
    repo.ensure_schema(conn)
    rows = repo.list_turn_files(conn, ids, project_id=project_id,
                                conversation_id=conversation_id)
    if len(rows) != len(ids):
        raise ValueError("one or more attachments are unavailable in this conversation")
    evidence = []
    for row in rows:
        if row.get("extraction") not in ("ready", "stripped") or row.get("kind") != "document":
            continue
        path = (Path(root) / str(row.get("rel_path") or "")).resolve()
        expected = (Path(root) / "data" / "projects" / project_id / "files").resolve()
        if expected not in path.parents or not path.is_file():
            raise ValueError("selected attachment is unavailable; upload it again")
        try:
            raw = path.read_bytes()
            actual_sniff = sniff.sniff_bytes(raw, row["original_name"])
        except Exception as exc:
            raise ValueError("selected attachment failed integrity validation; upload it again") from exc
        if (len(raw) != int(row.get("size") or -1)
                or hashing.sha256_hex(raw) != str(row.get("sha256") or "")
                or actual_sniff.mime != row.get("mime_detected")
                or actual_sniff.kind != row.get("kind")):
            raise ValueError("selected attachment failed integrity validation; upload it again")
        try:
            extracted = extract.extract(raw, actual_sniff.mime, row["original_name"])
        except Exception as exc:
            raise ValueError("selected attachment could not be safely extracted") from exc
        text = str(getattr(extracted, "text", "") or "")
        if getattr(extracted, "status", "") not in ("ready", "stripped") or not text or find_secrets(text):
            continue
        evidence.append({"file_id": row["file_id"], "path": row["safe_name"],
                         "filename": row["safe_name"], "scope": "conversation",
                         "conversation_id": conversation_id, "project_id": project_id,
                         "chunk_id": f"attachment:{row['file_id']}", "source_id": row["file_id"],
                         "text": text[:12000], "source": "turn_attachment"})
    return evidence
