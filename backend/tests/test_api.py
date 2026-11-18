"""The HTTP API end to end, with the worker run in-process."""
from fastapi.testclient import TestClient

from app import worker
from app.main import app
from tests.conftest import SAMPLES

AUTH = ("admin", "admin")


def test_api_requires_login(database):
    with TestClient(app) as client:
        assert client.get("/api/documents").status_code == 401
        assert client.post("/api/ask", json={"question": "anything"}).status_code == 401
        assert client.post("/api/login", json={"username": "admin", "password": "wrong"}).status_code == 401
        assert client.post("/api/login", json={"username": "admin", "password": "admin"}).status_code == 200
        assert client.get("/api/documents").status_code == 200       # session cookie now set


def test_upload_then_ask_over_http(database):
    with TestClient(app) as client:
        files = [("files", (name, (SAMPLES / name).read_bytes()))
                 for name in ("password-and-mfa-policy.pdf", "incident-response-procedure.md")]
        files.append(("files", ("notes.txt", b"not accepted")))
        results = client.post("/api/documents", files=files, auth=AUTH).json()["results"]
        assert [r["outcome"] for r in results] == ["created", "created", "rejected"]

        while (job := worker.claim()) is not None:
            worker.run_job(job)
        docs = client.get("/api/documents", auth=AUTH).json()["documents"]
        assert {d["status"] for d in docs} == {"ready"}

        answer = client.post("/api/ask", json={"question": "How long must a password be?"}, auth=AUTH).json()
        assert answer["supported"] is True
        assert answer["sources"][0]["location"]["clause"] == "2.1"

        declined = client.post("/api/ask", json={"question": "What is the parental leave policy?"}, auth=AUTH).json()
        assert declined["supported"] is False and declined["sources"] == []


def test_a_malformed_session_cookie_is_a_401_not_a_server_error(database):
    with TestClient(app) as client:
        client.cookies.set("session", "admin|9999999999|not-a-real-signature")
        assert client.get("/api/documents").status_code == 401


def test_a_session_cookie_with_odd_characters_is_simply_invalid():
    from app import auth
    assert auth._valid_token("caf\u00e9|9999999999|sig") is False
    assert auth._valid_token("admin|not-a-number|sig") is False


def test_an_empty_or_spaces_only_question_is_rejected(database):
    with TestClient(app) as client:
        for blank in ["", "   ", "\n\t"]:
            assert client.post("/api/ask", json={"question": blank}, auth=AUTH).status_code == 422
        assert client.post("/api/ask", json={"question": "x" * 501}, auth=AUTH).status_code == 422
        # Spaces around a real question are ignored.
        padded = client.post("/api/ask", json={"question": "  How long must a password be?  "}, auth=AUTH)
        assert padded.status_code == 200 and padded.json()["question"] == "How long must a password be?"
