"""Background indexing worker. Run with: python -m app.worker

Crash safety comes from three things:

1. The uploaded bytes and the job row commit together, so a job never points at a
   file that was not saved.
2. A job is claimed with a lease. If the worker dies, the lease runs out and the next
   worker (or the same one after restart) claims the job again.
3. All passages of a version are written, and the document switched to that version,
   in one transaction. A crash part-way leaves the previous state untouched, so
   running a job twice is harmless.
"""
import logging
import os
import socket
import threading
import time

from . import db
from .config import settings
from .extract import UnsupportedDocument, extract

log = logging.getLogger("worker")
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"

CLAIM_SQL = """
UPDATE jobs
SET state = 'running', attempts = attempts + 1, worker = %(worker)s,
    leased_until = now() + make_interval(secs => %(lease)s)
WHERE id = (
    SELECT id FROM jobs
    WHERE (state = 'queued' AND run_after <= now())
       OR (state = 'running' AND leased_until < now())
    ORDER BY id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING id, version_id, attempts
"""


class Superseded(Exception):
    """A newer upload of the same document arrived; this job's result is not needed."""


def claim() -> dict | None:
    with db.pool().connection() as conn:
        return conn.execute(CLAIM_SQL, {"worker": WORKER_ID, "lease": settings.lease_seconds}).fetchone()


def _heartbeat(job_id: int, stop: threading.Event) -> None:
    """Keeps the lease alive while a long document is being processed."""
    while not stop.wait(settings.lease_seconds / 3):
        try:
            with db.pool().connection() as conn:
                conn.execute(
                    "UPDATE jobs SET leased_until = now() + make_interval(secs => %s) "
                    "WHERE id = %s AND state = 'running' AND worker = %s",
                    (settings.lease_seconds, job_id, WORKER_ID))
        except Exception:
            log.exception("heartbeat failed")


def _set_stage(version_id: int, stage: str) -> None:
    with db.pool().connection() as conn:
        updated = conn.execute(
            "UPDATE document_versions SET status = %s, error = NULL "
            "WHERE id = %s AND status <> 'superseded'", (stage, version_id)).rowcount
    if not updated:
        raise Superseded()
    if settings.index_delay_seconds:
        time.sleep(settings.index_delay_seconds)


def index_version(version_id: int) -> None:
    with db.pool().connection() as conn:
        row = conn.execute(
            "SELECT v.content, v.status, d.filename, d.id AS document_id "
            "FROM document_versions v JOIN documents d ON d.id = v.document_id "
            "WHERE v.id = %s", (version_id,)).fetchone()
    if row is None or row["status"] == "superseded" or row["content"] is None:
        raise Superseded()

    _set_stage(version_id, "extracting")
    extracted = extract(bytes(row["content"]), row["filename"])

    _set_stage(version_id, "chunking")
    rows = [(version_id, i, p.kind, p.text, p.heading, p.page, p.clause, p.line)
            for i, p in enumerate(extracted.passages)]

    _set_stage(version_id, "indexing")
    with db.pool().connection() as conn:   # one transaction: all or nothing
        still_wanted = conn.execute(
            "SELECT 1 FROM document_versions WHERE id = %s AND status <> 'superseded' FOR UPDATE",
            (version_id,)).fetchone()
        if not still_wanted:
            raise Superseded()
        conn.execute("DELETE FROM chunks WHERE version_id = %s", (version_id,))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks (version_id, ordinal, kind, text, heading, page, clause, line) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", rows)
        conn.execute(
            "UPDATE document_versions SET status = 'ready', error = NULL, title = %s, "
            "doc_version = %s, doc_owner = %s, chunk_count = %s, indexed_at = now() WHERE id = %s",
            (extracted.title, extracted.doc_version, extracted.doc_owner, len(rows), version_id))
        # Switch the document to this version and drop what the old versions left behind.
        conn.execute("UPDATE documents SET active_version_id = %s WHERE id = %s",
                     (version_id, row["document_id"]))
        conn.execute(
            "DELETE FROM chunks WHERE version_id IN (SELECT id FROM document_versions "
            "WHERE document_id = %s AND id <> %s)", (row["document_id"], version_id))
        conn.execute(
            "UPDATE document_versions SET status = 'superseded', content = NULL "
            "WHERE document_id = %s AND id <> %s AND status <> 'failed'",
            (row["document_id"], version_id))
        db.bump_generation(conn)


def _finish(job_id: int, state: str, error: str | None = None, retry_in: float = 0) -> None:
    with db.pool().connection() as conn:
        conn.execute(
            "UPDATE jobs SET state = %s, last_error = %s, leased_until = NULL, "
            "run_after = now() + make_interval(secs => %s) WHERE id = %s",
            (state, error, retry_in, job_id))


def _fail_version(version_id: int, error: str) -> None:
    with db.pool().connection() as conn:
        conn.execute("UPDATE document_versions SET status = 'failed', error = %s "
                     "WHERE id = %s AND status <> 'superseded'", (error, version_id))


def run_job(job: dict) -> None:
    job_id, version_id, attempts = job["id"], job["version_id"], job["attempts"]
    if attempts > settings.max_attempts:
        # The worker died on this job every time it tried. Stop retrying.
        _fail_version(version_id, "Indexing was abandoned after repeated worker crashes.")
        _finish(job_id, "failed", "attempts exhausted")
        return

    stop = threading.Event()
    threading.Thread(target=_heartbeat, args=(job_id, stop), daemon=True).start()
    try:
        index_version(version_id)
        _finish(job_id, "done")
        log.info("job %s: version %s indexed (attempt %s)", job_id, version_id, attempts)
    except Superseded:
        _finish(job_id, "done", "superseded by a newer upload")
        log.info("job %s: superseded", job_id)
    except UnsupportedDocument as exc:
        _fail_version(version_id, str(exc))
        _finish(job_id, "failed", str(exc))
        log.info("job %s: rejected: %s", job_id, exc)
    except Exception as exc:
        log.exception("job %s failed (attempt %s)", job_id, attempts)
        message = f"{type(exc).__name__}: {exc}"
        if attempts >= settings.max_attempts:
            _fail_version(version_id, f"Indexing failed after {attempts} attempts. {message}")
            _finish(job_id, "failed", message)
        else:
            _finish(job_id, "queued", message, retry_in=2 ** attempts)
    finally:
        stop.set()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    db.wait_for_db()
    db.init_schema()
    log.info("worker %s started (lease %ss)", WORKER_ID, settings.lease_seconds)
    while True:
        try:
            job = claim()
        except Exception:
            log.exception("could not claim a job")
            time.sleep(2)
            continue
        if job is None:
            time.sleep(settings.poll_seconds)
            continue
        run_job(job)


if __name__ == "__main__":
    main()
