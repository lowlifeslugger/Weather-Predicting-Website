from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_owned_notebook
from app.db.models import Note, Notebook
from app.schemas.note import NoteCreate, NoteOut, NoteUpdate
from app.services.indexing import index_note, remove_note_chunks

router = APIRouter(prefix="/notebooks/{notebook_id}/notes", tags=["notes"])


def _get_note_or_404(db: Session, notebook: Notebook, note_id: UUID) -> Note:
    note = db.query(Note).filter(Note.id == note_id, Note.notebook_id == notebook.id).first()
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return note


@router.get("", response_model=List[NoteOut])
def list_notes(notebook: Notebook = Depends(get_owned_notebook), db: Session = Depends(get_db)):
    return (
        db.query(Note)
        .filter(Note.notebook_id == notebook.id)
        .order_by(Note.updated_at.desc())
        .all()
    )


@router.post("", response_model=NoteOut, status_code=status.HTTP_201_CREATED)
def create_note(
    payload: NoteCreate,
    notebook: Notebook = Depends(get_owned_notebook),
    db: Session = Depends(get_db),
):
    note = Note(
        notebook_id=notebook.id,
        title=payload.title or "Untitled note",
        content=payload.content or "",
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    index_note(notebook, note)
    return note


@router.get("/{note_id}", response_model=NoteOut)
def get_note(
    note_id: UUID,
    notebook: Notebook = Depends(get_owned_notebook),
    db: Session = Depends(get_db),
):
    return _get_note_or_404(db, notebook, note_id)


@router.patch("/{note_id}", response_model=NoteOut)
def update_note(
    note_id: UUID,
    payload: NoteUpdate,
    notebook: Notebook = Depends(get_owned_notebook),
    db: Session = Depends(get_db),
):
    note = _get_note_or_404(db, notebook, note_id)
    if payload.title is not None:
        note.title = payload.title
    if payload.content is not None:
        note.content = payload.content
    db.commit()
    db.refresh(note)
    index_note(notebook, note)  # always re-index -- content may have changed
    return note


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(
    note_id: UUID,
    notebook: Notebook = Depends(get_owned_notebook),
    db: Session = Depends(get_db),
):
    note = _get_note_or_404(db, notebook, note_id)
    remove_note_chunks(notebook, note.id)
    db.delete(note)
    db.commit()
