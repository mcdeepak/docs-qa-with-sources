"""Test setup. Integration tests use their own database so they never touch real data."""
import os
from pathlib import Path

import psycopg
import pytest

BASE_URL = os.environ.get("DATABASE_URL", "postgresql://docsqa:docsqa@db:5432/docsqa")
TEST_URL = BASE_URL.rsplit("/", 1)[0] + "/docsqa_test"
os.environ["DATABASE_URL"] = TEST_URL          # must be set before app.config is imported
os.environ["WORKER_LEASE_SECONDS"] = "20"
os.environ["INDEX_DELAY_SECONDS"] = "0"

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
CORPUS = ROOT / "eval" / "corpus"


def _database_available() -> bool:
    try:
        with psycopg.connect(BASE_URL, connect_timeout=2, autocommit=True) as conn:
            exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = 'docsqa_test'").fetchone()
            if not exists:
                conn.execute("CREATE DATABASE docsqa_test")
        return True
    except psycopg.OperationalError:
        return False


@pytest.fixture
def database():
    """A clean test database. Tests that need it are skipped when Postgres is not reachable."""
    if not _database_available():
        pytest.skip("Postgres is not reachable")
    from app import db, index

    db.init_schema()
    with db.pool().connection() as conn:
        conn.execute("TRUNCATE documents, document_versions, chunks, jobs RESTART IDENTITY CASCADE")
        conn.execute("UPDATE index_state SET generation = generation + 1")
    index._cached = None
    return db


@pytest.fixture(scope="session")
def sample_index():
    """An in-memory index of the five sample documents. No database needed."""
    from app.extract import extract
    from app.index import Chunk, CorpusIndex

    chunks, cid = [], 0
    files = [f for f in sorted(SAMPLES.iterdir()) if f.name != "sample-questions.md"]
    for doc_id, f in enumerate(files):
        ex = extract(f.read_bytes(), f.name)
        for i, p in enumerate(ex.passages):
            cid += 1
            chunks.append(Chunk(cid, doc_id, f.name, ex.title, ex.doc_version, ex.doc_owner, 1,
                                "", i, p.kind, p.text, p.heading, p.page, p.clause, p.line))
    return CorpusIndex(chunks)
