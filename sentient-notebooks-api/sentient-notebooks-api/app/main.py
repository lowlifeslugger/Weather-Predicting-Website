from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import auth, chat, documents, notebooks, notes
from app.db.base import Base, engine

# Dev convenience: auto-creates tables on startup if they don't exist.
# Once this is running for real, swap this for Alembic migrations instead
# (auto-create doesn't handle schema changes safely).
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Sentient Notebooks API")

app.include_router(auth.router)
app.include_router(notebooks.router)
app.include_router(documents.router)
app.include_router(notes.router)
app.include_router(chat.router)


@app.get("/health")
def health():
    return {"status": "ok"}


# Mounted last and at "/" -- API routes above are matched first since
# they're registered first, this just catches everything else and serves
# the frontend (html=True means "/" serves index.html automatically).
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
