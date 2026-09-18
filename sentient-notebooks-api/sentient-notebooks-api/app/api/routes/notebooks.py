from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_owned_notebook
from app.core.config import settings
from app.db.models import Notebook, User
from app.schemas.notebook import NotebookCreate, NotebookOut, NotebookUpdate
from app.services.chroma_client import delete_notebook_collection
from app.services.indexing import index_note, reindex_document
from app.services.storage import get_storage

router = APIRouter(prefix="/notebooks", tags=["notebooks"])


@router.post("", response_model=NotebookOut, status_code=status.HTTP_201_CREATED)
def create_notebook(
    payload: NotebookCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing_count = db.query(Notebook).filter(Notebook.user_id == current_user.id).count()
    if existing_count >= settings.max_notebooks_per_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You already have {existing_count} notebook(s) -- "
            f"this account is limited to {settings.max_notebooks_per_user}.",
        )

    notebook = Notebook(
        user_id=current_user.id,
        name=payload.name,
        llm_model=payload.llm_model or settings.default_llm_model,
    )
    db.add(notebook)
    db.commit()
    db.refresh(notebook)
    return notebook


@router.get("", response_model=List[NotebookOut])
def list_notebooks(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(Notebook)
        .filter(Notebook.user_id == current_user.id)
        .order_by(Notebook.created_at.desc())
        .all()
    )


@router.get("/{notebook_id}", response_model=NotebookOut)
def get_notebook(notebook: Notebook = Depends(get_owned_notebook)):
    return notebook


@router.patch("/{notebook_id}", response_model=NotebookOut)
def update_notebook(
    payload: NotebookUpdate,
    notebook: Notebook = Depends(get_owned_notebook),
    db: Session = Depends(get_db),
):
    if payload.name is not None:
        notebook.name = payload.name
    if payload.llm_model is not None:
        notebook.llm_model = payload.llm_model
    db.commit()
    db.refresh(notebook)
    return notebook


@router.delete("/{notebook_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_notebook(
    notebook: Notebook = Depends(get_owned_notebook),
    db: Session = Depends(get_db),
):
    storage = get_storage()
    for doc in notebook.documents:
        if doc.storage_key:
            try:
                storage.delete(doc.storage_key)
            except Exception:
                pass

    delete_notebook_collection(notebook.id)
    db.delete(notebook)  # cascades to Document and Note rows
    db.commit()


@router.post("/{notebook_id}/reindex", status_code=status.HTTP_204_NO_CONTENT)
def reindex_notebook(
    notebook: Notebook = Depends(get_owned_notebook),
    db: Session = Depends(get_db),
):
    """Rebuilds this notebook's Chroma collection from scratch, using the
    raw files in storage and the notes already in the DB. Call this if
    search/chat stops finding content that should be there -- most likely
    cause is the vector index living on disk that got wiped (e.g. a free
    host's ephemeral filesystem after a redeploy).
    """
    delete_notebook_collection(notebook.id)

    storage = get_storage()
    for doc in notebook.documents:
        if not doc.storage_key:
            continue
        try:
            raw = storage.load(doc.storage_key)
        except Exception:
            continue  # file's gone from storage too -- nothing to rebuild from
        reindex_document(notebook, doc, raw)

    for note in notebook.notes:
        index_note(notebook, note)
