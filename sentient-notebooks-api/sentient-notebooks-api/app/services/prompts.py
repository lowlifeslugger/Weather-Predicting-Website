from typing import Dict, List


def build_prompt(query: str, chunks: List[Dict]) -> str:
    context = "\n\n---\n\n".join(f"[Source: {c['source']}]\n{c['text']}" for c in chunks)
    return (
        "You are a precise, helpful assistant. "
        "Answer the question using ONLY the provided context. "
        "If the answer is not in the context, say so clearly — do not make things up.\n\n"
        f"CONTEXT:\n{context}\n\n"
        f"QUESTION: {query}\n\n"
        "ANSWER:"
    )


def build_web_prompt(query: str, results: List[Dict]) -> str:
    context = "\n\n---\n\n".join(f"[URL: {r['url']}]\n{r['text']}" for r in results)
    return (
        "You are a helpful assistant with access to live web search results. "
        "Answer the question using the provided web content. "
        "Be thorough and cite which source supports each point.\n\n"
        f"WEB CONTENT:\n{context}\n\n"
        f"QUESTION: {query}\n\n"
        "ANSWER:"
    )


def build_both_prompt(query: str, chunks: List[Dict], web_results: List[Dict]) -> str:
    local_ctx = "\n\n---\n\n".join(f"[Local — {c['source']}]\n{c['text']}" for c in chunks)
    web_ctx = "\n\n---\n\n".join(f"[Web — {r['url']}]\n{r['text']}" for r in web_results)
    return (
        "You are a helpful assistant. Answer using BOTH the local documents AND the web results below. "
        "Clearly distinguish when you are drawing from local files vs web sources. "
        "Do not make things up.\n\n"
        f"LOCAL DOCUMENTS:\n{local_ctx}\n\n"
        f"WEB RESULTS:\n{web_ctx}\n\n"
        f"QUESTION: {query}\n\n"
        "ANSWER:"
    )
