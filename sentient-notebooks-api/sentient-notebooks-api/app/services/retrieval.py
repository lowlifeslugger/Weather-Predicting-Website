from typing import Dict, List

from app.core.config import settings
from app.services.chroma_client import get_notebook_collection
from app.services.embedding import embed_texts


def retrieve(notebook_id, query: str) -> List[Dict]:
    collection = get_notebook_collection(notebook_id)
    n = min(settings.top_k, collection.count())
    if n == 0:
        return []

    vec = embed_texts([query])
    res = collection.query(
        query_embeddings=vec,
        n_results=n,
        include=["documents", "metadatas", "distances"],
    )

    results = []
    for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
        results.append(
            {
                "text": doc,
                "source": meta.get("source", "unknown"),
                "score": round(1.0 - float(dist), 3),
            }
        )
    return results
