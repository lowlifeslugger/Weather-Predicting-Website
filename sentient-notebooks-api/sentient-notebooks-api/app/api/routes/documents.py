from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_owned_notebook
from app.core.config import settings
from app.db.models import Document, Notebook
from app.schemas.document import DocumentOut
from app.services.documents import file_hash, is_supported
from app.services.indexing import index_document, remove_document
from app.services.quota import get_user_storage_usage
from app.services.storage import get_storage

router = APIRouter(prefix="/notebooks/{notebook_id}/documents", tags=["documents"])


@router.get("", response_model=List[DocumentOut])
def list_documents(
    notebook: Notebook = Depends(get_owned_notebook),
    db: Session = Depends(get_db),
):
    return (
        db.query(Document)
        .filter(Document.notebook_id == notebook.id)
        .order_by(Document.filename)
        .all()
    )


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    notebook: Notebook = Depends(get_owned_notebook),
    db: Session = Depends(get_db),
):
    if not is_supported(file.filename):
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Use .pdf, .txt, .md, .markdown, or .rst",
        )

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")

    h = file_hash(raw)

    existing = (
        db.query(Document)
        .filter(Document.notebook_id == notebook.id, Document.filename == file.filename)
        .first()
    )

    # Same hash-skip logic as rag.py's index_docs() -- unchanged file,
    # nothing to redo (and no point re-checking quota for a no-op).
    if existing and existing.file_hash == h:
        return existing

    # Quota check. If we're replacing an existing file, its current bytes
    # are about to be freed, so don't count them against the new upload.
    current_usage = get_user_storage_usage(db, notebook.user_id)
    if existing:
        current_usage -= existing.file_size
    if current_usage + len(raw) > settings.max_storage_bytes_per_user:
        remaining = max(0, settings.max_storage_bytes_per_user - current_usage)
        raise HTTPException(
            status_code=413,
            detail=(
                f"Storage quota exceeded. You have {remaining / 1024 / 1024:.1f}MB left "
                f"of your {settings.max_storage_bytes_per_user / 1024 / 1024:.0f}MB limit."
            ),
        )

    if existing:
        remove_document(db, notebook, existing)

    return index_document(db, notebook, file.filename, raw)


@router.get("/{document_id}/download")
def download_document(
    document_id: UUID,
    notebook: Notebook = Depends(get_owned_notebook),
    db: Session = Depends(get_db),
):
    doc = (
        db.query(Document)
        .filter(Document.id == document_id, Document.notebook_id == notebook.id)
        .first()
    )
    if doc is None or not doc.storage_key:
        raise HTTPException(status_code=404, detail="Document not found")

    try:
        raw = get_storage().load(doc.storage_key)
    except Exception:
        raise HTTPException(status_code=404, detail="File is missing from storage")

    return Response(
        content=raw,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{doc.filename}"'},
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: UUID,
    notebook: Notebook = Depends(get_owned_notebook),
    db: Session = Depends(get_db),
):
    doc = (
        db.query(Document)
        .filter(Document.id == document_id, Document.notebook_id == notebook.id)
        .first()
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    remove_document(db, notebook, doc)
