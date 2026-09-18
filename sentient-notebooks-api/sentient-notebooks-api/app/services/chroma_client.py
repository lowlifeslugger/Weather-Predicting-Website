import chromadb

from app.core.config import settings

_client = None


def get_chroma_client():
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=settings.chroma_dir)
    return _client


def _collection_name(notebook_id) -> str:
    # hex (no dashes) to stay safely inside Chroma's allowed collection-name
    # charset regardless of client version.
    return f"nb_{notebook_id.hex}"


def get_notebook_collection(notebook_id):
    """One Chroma collection per notebook == the multi-tenancy boundary.
    A query against notebook A's collection can never surface notebook B's
    chunks, even if B belongs to a different user -- there's no shared
    collection with a filter to get wrong.
    """
    client = get_chroma_client()
    return client.get_or_create_collection(
        _collection_name(notebook_id),
        metadata={"hnsw:space": "cosine"},
    )


def delete_notebook_collection(notebook_id):
    client = get_chroma_client()
    try:
        client.delete_collection(_collection_name(notebook_id))
    except Exception:
        # Collection may never have been created (notebook with 0 docs) --
        # that's fine, nothing to clean up.
        pass
