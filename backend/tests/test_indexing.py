"""Upload rules and the worker, against a real Postgres. Skipped if it is not reachable."""
from app import documents, index, qa, worker
from tests.conftest import SAMPLES

RETENTION = (SAMPLES / "data-retention-policy.pdf").read_bytes()
DEVICE = (SAMPLES / "device-and-laptop-policy.md").read_bytes()


def drain():
    """Run queued jobs the way the worker loop does."""
    while (job := worker.claim()) is not None:
        worker.run_job(job)


def count(db, sql, *args):
    with db.pool().connection() as conn:
        return conn.execute(sql, args).fetchone()["count"]


def status_of(filename):
    return next(d for d in documents.list_documents() if d["filename"] == filename)


def test_upload_is_indexed_by_the_worker_and_becomes_searchable(database):
    result = documents.upload("data-retention-policy.pdf", RETENTION)
    assert result["outcome"] == "created" and result["status"] == "queued"
    assert qa.ask("How long are backups kept?")["supported"] is False   # not indexed yet

    drain()
    doc = status_of("data-retention-policy.pdf")
    assert doc["status"] == "ready" and doc["chunk_count"] == 7 and doc["doc_version"] == "1.4"
    answer = qa.ask("How long are backups kept?")
    assert answer["supported"] is True and "35 days" in answer["answer"]


def test_uploading_the_same_file_again_creates_nothing(database):
    documents.upload("data-retention-policy.pdf", RETENTION)
    drain()
    again = documents.upload("data-retention-policy.pdf", RETENTION)
    assert again["outcome"] == "duplicate"
    assert count(database, "SELECT count(*) FROM document_versions") == 1
    assert count(database, "SELECT count(*) FROM jobs") == 1
    assert count(database, "SELECT count(*) FROM chunks") == 7


def test_the_same_content_under_another_name_creates_nothing(database):
    documents.upload("data-retention-policy.pdf", RETENTION)
    copy = documents.upload("copy of retention.pdf", RETENTION)
    assert copy["outcome"] == "duplicate" and copy["filename"] == "data-retention-policy.pdf"
    assert count(database, "SELECT count(*) FROM documents") == 1


def test_a_changed_file_replaces_the_old_version_cleanly(database):
    documents.upload("device-and-laptop-policy.md", DEVICE)
    drain()
    assert "every 4 years" in qa.ask("How often are laptops refreshed?")["answer"]

    changed = DEVICE.replace(b"every 4 years", b"every 3 years").replace(b"**Version:** 2.0", b"**Version:** 2.1")
    result = documents.upload("device-and-laptop-policy.md", changed)
    assert result["outcome"] == "replaced" and result["version_no"] == 2

    # Until the worker finishes, the old version keeps answering.
    assert "every 4 years" in qa.ask("How often are laptops refreshed?")["answer"]

    drain()
    answer = qa.ask("How often are laptops refreshed?")
    assert "every 3 years" in answer["answer"]
    assert answer["sources"][0]["document_version"] == "2.1"
    assert answer["sources"][0]["upload_no"] == 2
    # Nothing of the old version is left in the index.
    assert count(database, "SELECT count(*) FROM documents") == 1
    assert count(database, "SELECT count(*) FROM chunks WHERE text LIKE %s", "%every 4 years%") == 0
    assert count(database, "SELECT count(*) FROM chunks") == 7


def test_a_crashed_worker_does_not_lose_the_job(database):
    documents.upload("data-retention-policy.pdf", RETENTION)

    # A worker claims the job and then dies: it never finishes and never releases.
    crashed = worker.claim()
    assert crashed["attempts"] == 1
    assert worker.claim() is None                      # the lease protects the job meanwhile

    # Time passes and the lease runs out.
    with database.pool().connection() as conn:
        conn.execute("UPDATE jobs SET leased_until = now() - interval '1 second'")

    recovered = worker.claim()
    assert recovered["id"] == crashed["id"] and recovered["attempts"] == 2
    worker.run_job(recovered)
    assert status_of("data-retention-policy.pdf")["status"] == "ready"
    assert count(database, "SELECT count(*) FROM chunks") == 7     # indexed once, not twice


def test_a_crash_part_way_through_indexing_leaves_no_partial_data(database, monkeypatch):
    documents.upload("data-retention-policy.pdf", RETENTION)

    def die(conn):
        raise RuntimeError("power cut")

    # Fail at the very last step of the indexing transaction.
    monkeypatch.setattr(worker.db, "bump_generation", die)
    worker.run_job(worker.claim())
    assert count(database, "SELECT count(*) FROM chunks") == 0     # the transaction rolled back
    monkeypatch.undo()

    with database.pool().connection() as conn:
        conn.execute("UPDATE jobs SET run_after = now()")           # skip the retry back-off
    drain()
    assert status_of("data-retention-policy.pdf")["status"] == "ready"
    assert count(database, "SELECT count(*) FROM chunks") == 7


def test_an_unreadable_file_fails_with_a_reason_and_is_not_retried(database):
    documents.upload("broken.pdf", b"%PDF-1.4 this is not a real pdf")
    drain()
    doc = status_of("broken.pdf")
    assert doc["status"] == "failed" and "could not be read" in doc["error"]
    assert count(database, "SELECT count(*) FROM jobs WHERE state = 'failed' AND attempts = 1") == 1


def test_a_failed_replacement_keeps_the_previous_version_searchable(database):
    documents.upload("data-retention-policy.pdf", RETENTION)
    drain()
    documents.upload("data-retention-policy.pdf", b"%PDF-1.4 corrupted upload")
    drain()
    doc = status_of("data-retention-policy.pdf")
    assert doc["status"] == "failed" and doc["previous_version_live"] is True
    assert qa.ask("How long are backups kept?")["supported"] is True


def test_deleting_a_document_removes_it_from_answers(database):
    documents.upload("data-retention-policy.pdf", RETENTION)
    drain()
    documents.delete(status_of("data-retention-policy.pdf")["id"])
    assert qa.ask("How long are backups kept?")["supported"] is False
    assert count(database, "SELECT count(*) FROM chunks") == 0


def test_the_index_is_only_rebuilt_when_documents_change(database):
    documents.upload("data-retention-policy.pdf", RETENTION)
    drain()
    first = index.current()
    assert index.current() is first
    documents.upload("device-and-laptop-policy.md", DEVICE)
    drain()
    assert index.current() is not first


def test_a_finished_job_that_runs_again_cannot_undo_a_newer_upload(database):
    """Regression: a job re-run after its lease lapsed used to retire the newer version."""
    documents.upload("device-and-laptop-policy.md", DEVICE)
    first = worker.claim()
    worker.run_job(first)                                   # version 1 is ready

    changed = DEVICE.replace(b"every 4 years", b"every 3 years")
    documents.upload("device-and-laptop-policy.md", changed)   # version 2 is queued

    # The first job comes back as if its lease had lapsed before it was marked done.
    with database.pool().connection() as conn:
        conn.execute("UPDATE jobs SET state = 'running', leased_until = now() - interval '1 second' "
                     "WHERE id = %s", (first["id"],))
    rerun = worker.claim()
    assert rerun["id"] == first["id"]
    worker.run_job(rerun)

    assert status_of("device-and-laptop-policy.md")["status"] == "queued"   # version 2 untouched
    drain()
    doc = status_of("device-and-laptop-policy.md")
    assert doc["status"] == "ready" and doc["version_no"] == 2
    assert "every 3 years" in qa.ask("How often are laptops refreshed?")["answer"]
