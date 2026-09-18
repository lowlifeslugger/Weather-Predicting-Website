from sqlalchemy.orm import Session

from app.db.models import Document, Note, Notebook
from app.services.chroma_client import get_notebook_collection
from app.services.documents import chunk_text, extract_text, file_hash
from app.services.embedding import embed_texts
from app.services.storage import get_storage


def index_document(db: Session, notebook: Notebook, filename: str, raw: bytes) -> Document:
    """Chunk, embed, and store a file's contents in the notebook's Chroma
    collection, save the raw bytes to the storage backend, then record it
    in the DB. Mirrors rag.py's index_docs(), just for one uploaded file
    instead of a whole folder scan.
    """
    h = file_hash(raw)
    text = extract_text(filename, raw)
    chunks = chunk_text(text) if text.strip() else []

    if chunks:
        collection = get_notebook_collection(notebook.id)
        vectors = embed_texts(chunks)
        ids = [f"{filename}||{h}||chunk_{i}" for i in range(len(chunks))]
        metadatas = [{"source": filename, "chunk_index": i} for i in range(len(chunks))]
        collection.add(embeddings=vectors, documents=chunks, metadatas=metadatas, ids=ids)

    storage_key = f"{notebook.id}/{h}/{filename}"
    get_storage().save(storage_key, raw)

    doc = Document(
        notebook_id=notebook.id,
        filename=filename,
        file_hash=h,
        file_size=len(raw),
        storage_key=storage_key,
        chunk_count=len(chunks),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def remove_document(db: Session, notebook: Notebook, document: Document) -> None:
    """Removes a document's chunks from Chroma, its raw file from storage,
    and its row from the DB."""
    collection = get_notebook_collection(notebook.id)
    try:
        existing = collection.get(where={"source": document.filename})
        if existing.get("ids"):
            collection.delete(ids=existing["ids"])
    except Exception:
        pass

    if document.storage_key:
        try:
            get_storage().delete(document.storage_key)
        except Exception:
            pass

    db.delete(document)
    db.commit()


def reindex_document(notebook: Notebook, document: Document, raw: bytes) -> None:
    """Rebuilds one document's chunks in Chroma from its stored raw bytes,
    without touching its DB row. Used to recover if the Chroma collection
    was lost -- e.g. an ephemeral disk on a free host wiping local files
    on redeploy. The DB (Postgres/sqlite) and the storage backend are the
    real source of truth; Chroma is a rebuildable index on top of them.
    """
    text = extract_text(document.filename, raw)
    chunks = chunk_text(text) if text.strip() else []
    if not chunks:
        return

    collection = get_notebook_collection(notebook.id)
    vectors = embed_texts(chunks)
    ids = [f"{document.filename}||{document.file_hash}||chunk_{i}" for i in range(len(chunks))]
    metadatas = [{"source": document.filename, "chunk_index": i} for i in range(len(chunks))]
    collection.add(embeddings=vectors, documents=chunks, metadatas=metadatas, ids=ids)


def index_note(notebook: Notebook, note: Note) -> None:
    """(Re)indexes a note's content into the notebook's Chroma collection.
    Call this after every create/update -- unlike documents (which skip
    reindexing on an unchanged hash), notes always clear + rewrite their
    chunks, since there's no separate "did the content change" check for
    freeform text a user is actively editing.
    """
    remove_note_chunks(notebook, note.id)

    chunks = chunk_text(note.content) if note.content.strip() else []
    if not chunks:
        return

    collection = get_notebook_collection(notebook.id)
    vectors = embed_texts(chunks)
    ids = [f"note::{note.id}::chunk_{i}" for i in range(len(chunks))]
    metadatas = [
        {"source": note.title, "note_id": str(note.id), "chunk_index": i} for i in range(len(chunks))
    ]
    collection.add(embeddings=vectors, documents=chunks, metadatas=metadatas, ids=ids)


def remove_note_chunks(notebook: Notebook, note_id) -> None:
    collection = get_notebook_collection(notebook.id)
    try:
        existing = collection.get(where={"note_id": str(note_id)})
        if existing.get("ids"):
            collection.delete(ids=existing["ids"])
    except Exception:
        pass
