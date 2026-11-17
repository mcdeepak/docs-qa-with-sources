"""Upload rules: no duplicates, clean replacement, and a queued job per new version.

A document is identified by its filename. Its content is identified by SHA-256.

- Same filename, same content        -> nothing new is created.
- Same content under another name    -> nothing new is created; we point at the original.
- Same filename, different content   -> a new version is queued. The current version
                                        keeps answering until the new one is ready.
"""
import hashlib

from . import db
from .extract import EXTRACTORS, extension


class UploadRejected(Exception):
    pass


LIST_SQL = """
SELECT d.id, d.filename, d.created_at,
       v.id AS version_id, v.version_no, v.status, v.error, v.title, v.doc_version,
       v.doc_owner, v.chunk_count, v.size_bytes, v.uploaded_at, v.indexed_at,
       d.active_version_id IS NOT NULL AND d.active_version_id <> v.id AS previous_version_live,
       j.attempts
FROM documents d
JOIN LATERAL (SELECT * FROM document_versions WHERE document_id = d.id
              ORDER BY version_no DESC LIMIT 1) v ON true
LEFT JOIN LATERAL (SELECT attempts FROM jobs WHERE version_id = v.id
                   ORDER BY id DESC LIMIT 1) j ON true
ORDER BY d.filename
"""


def list_documents(conn=None) -> list[dict]:
    if conn is None:
        with db.pool().connection() as c:
            return list_documents(c)
    rows = conn.execute(LIST_SQL).fetchall()
    for r in rows:
        for key in ("created_at", "uploaded_at", "indexed_at"):
            r[key] = r[key].isoformat() if r[key] else None
    return rows


def upload(filename: str, data: bytes) -> dict:
    filename = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not filename:
        raise UploadRejected("The file has no name.")
    if extension(filename) not in EXTRACTORS:
        raise UploadRejected(f"Unsupported file type. Accepted: {', '.join(sorted(EXTRACTORS))}.")
    if not data:
        raise UploadRejected("The file is empty.")
    sha = hashlib.sha256(data).hexdigest()

    with db.pool().connection() as conn:   # one transaction
        # Serialise uploads so two identical uploads at once cannot both get through.
        conn.execute("SELECT pg_advisory_xact_lock(727275)")

        doc = conn.execute("SELECT id FROM documents WHERE filename = %s FOR UPDATE",
                           (filename,)).fetchone()
        latest = None
        if doc:
            latest = conn.execute(
                "SELECT id, version_no, sha256, status FROM document_versions "
                "WHERE document_id = %s ORDER BY version_no DESC LIMIT 1", (doc["id"],)).fetchone()

        if latest and latest["sha256"] == sha and latest["status"] != "failed":
            return {"outcome": "duplicate", "document_id": doc["id"], "filename": filename,
                    "version_no": latest["version_no"], "status": latest["status"],
                    "message": "This exact file is already uploaded. Nothing was changed."}

        other = conn.execute(
            "SELECT d.id, d.filename, v.version_no, v.status FROM document_versions v "
            "JOIN documents d ON d.id = v.document_id "
            "WHERE v.sha256 = %s AND d.filename <> %s AND v.status NOT IN ('failed', 'superseded') "
            "LIMIT 1", (sha, filename)).fetchone()
        if other:
            return {"outcome": "duplicate", "document_id": other["id"], "filename": other["filename"],
                    "version_no": other["version_no"], "status": other["status"],
                    "message": f"This content is already uploaded as {other['filename']}. Nothing was changed."}

        if not doc:
            doc = conn.execute("INSERT INTO documents (filename) VALUES (%s) RETURNING id",
                               (filename,)).fetchone()
        version_no = (latest["version_no"] + 1) if latest else 1

        # A newer upload makes any still-unindexed older upload pointless.
        conn.execute(
            "UPDATE document_versions SET status = 'superseded', content = NULL "
            "WHERE document_id = %s AND status IN ('queued', 'extracting', 'chunking', 'indexing')",
            (doc["id"],))

        version = conn.execute(
            "INSERT INTO document_versions (document_id, version_no, sha256, content, size_bytes) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (doc["id"], version_no, sha, data, len(data))).fetchone()
        conn.execute("INSERT INTO jobs (version_id) VALUES (%s)", (version["id"],))

        return {"outcome": "replaced" if latest else "created", "document_id": doc["id"],
                "filename": filename, "version_no": version_no, "status": "queued",
                "message": ("A new version was queued. The current version stays searchable "
                            "until it is ready.") if latest else "Queued for indexing."}


def delete(document_id: int) -> bool:
    with db.pool().connection() as conn:
        deleted = conn.execute("DELETE FROM documents WHERE id = %s", (document_id,)).rowcount
        if deleted:
            db.bump_generation(conn)
        return bool(deleted)
