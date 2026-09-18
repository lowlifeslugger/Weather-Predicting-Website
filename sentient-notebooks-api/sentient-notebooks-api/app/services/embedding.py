from typing import List

_embedder = None


def get_embedder():
    """Loads the sentence-transformers model once and reuses it for every
    request. First call downloads the model (~90MB) from Hugging Face --
    same one-time download rag.py does -- then it's cached locally and
    works offline.
    """
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer

        from app.core.config import settings

        _embedder = SentenceTransformer(settings.embed_model)
    return _embedder


def embed_texts(texts: List[str]) -> List[List[float]]:
    return get_embedder().encode(texts, show_progress_bar=False).tolist()
