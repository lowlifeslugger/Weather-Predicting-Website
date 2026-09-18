from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Document, Notebook


def get_user_storage_usage(db: Session, user_id) -> int:
    """Sums file_size across every document in every notebook this user
    owns. Computed on demand rather than a running counter -- avoids the
    counter ever drifting out of sync with what's actually stored.
    """
    total = (
        db.query(func.coalesce(func.sum(Document.file_size), 0))
        .join(Notebook, Document.notebook_id == Notebook.id)
        .filter(Notebook.user_id == user_id)
        .scalar()
    )
    return int(total or 0)
