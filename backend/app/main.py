"""HTTP API and static UI. Run with: uvicorn app.main:app"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StringConstraints
from typing_extensions import Annotated

from . import auth, db, documents, qa
from .answering import get_answerer
from .config import settings
from .extract import EXTRACTORS
from .retrieval import get_retriever

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
STATIC = Path(__file__).parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail at startup, not on the first question, if the configuration is wrong.
    get_retriever(settings.retriever)
    get_answerer(settings.answerer)
    db.wait_for_db()
    db.init_schema()
    yield


app = FastAPI(title="Document Q&A with Sources", version="1.0", lifespan=lifespan)
protected = [Depends(auth.require_login)]


class Login(BaseModel):
    username: str
    password: str


class Question(BaseModel):
    # Surrounding spaces are removed first, so a question of only spaces is rejected (422).
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


@app.get("/api/health")
def health():
    return {"status": "ok", "retriever": settings.retriever, "answerer": settings.answerer}


@app.post("/api/login")
def login(body: Login, response: Response):
    if not auth.check_credentials(body.username, body.password):
        raise HTTPException(status_code=401, detail="Wrong username or password.")
    response.set_cookie(auth.COOKIE, auth.make_token(body.username), max_age=auth.MAX_AGE,
                        httponly=True, samesite="lax")
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


@app.get("/api/me", dependencies=protected)
def me():
    return {"username": settings.app_username, "retriever": settings.retriever,
            "answerer": settings.answerer, "accepted_types": sorted(EXTRACTORS),
            "max_upload_mb": settings.max_upload_mb}


@app.post("/api/documents", dependencies=protected)
async def upload_documents(files: list[UploadFile] = File(...)):
    """Upload one or more files. Returns at once; indexing happens in the worker."""
    results = []
    limit = settings.max_upload_mb * 1024 * 1024
    for f in files:
        # Read at most one byte past the limit, so an oversized file is never held whole.
        data = await f.read(limit + 1)
        if len(data) > limit:
            results.append({"outcome": "rejected", "filename": f.filename,
                            "message": f"Larger than {settings.max_upload_mb} MB."})
            continue
        try:
            results.append(await asyncio.to_thread(documents.upload, f.filename or "", data))
        except documents.UploadRejected as exc:
            results.append({"outcome": "rejected", "filename": f.filename, "message": str(exc)})
    return {"results": results}


@app.get("/api/documents", dependencies=protected)
def list_documents():
    return {"documents": documents.list_documents()}


@app.delete("/api/documents/{document_id}", dependencies=protected)
def delete_document(document_id: int):
    if not documents.delete(document_id):
        raise HTTPException(status_code=404, detail="No such document.")
    return {"ok": True}


@app.get("/api/documents/events", dependencies=protected)
async def document_events(request: Request):
    """Server-sent events: the document list, sent whenever it changes."""
    async def stream():
        last = None
        idle = 0
        while not await request.is_disconnected():
            docs = await asyncio.to_thread(documents.list_documents)
            payload = json.dumps({"documents": docs})
            if payload != last:
                last, idle = payload, 0
                yield f"data: {payload}\n\n"
            else:
                idle += 1
                if idle % 30 == 0:
                    yield ": keep-alive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/ask", dependencies=protected)
def ask(body: Question):
    return qa.ask(body.question)


# The built UI. Any path that is not an API route or a file returns the app shell.
if STATIC.exists():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def ui(path: str):
        if path.startswith("api/"):
            raise HTTPException(status_code=404)
        return FileResponse(STATIC / "index.html")
