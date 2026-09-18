import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.api.deps import get_owned_notebook
from app.db.models import Notebook
from app.schemas.chat import ChatRequest
from app.services.llm import stream_chat
from app.services.prompts import build_both_prompt, build_prompt, build_web_prompt
from app.services.retrieval import retrieve
from app.services.web_search import retrieve_web, web_search_available

router = APIRouter(prefix="/notebooks/{notebook_id}/chat", tags=["chat"])


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("")
def chat(payload: ChatRequest, notebook: Notebook = Depends(get_owned_notebook)):
    query = payload.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query cannot be empty")

    if payload.mode in ("web", "both") and not web_search_available():
        raise HTTPException(
            status_code=503,
            detail="Web search isn't installed on the server (needs ddgs + trafilatura).",
        )

    def event_stream():
        # ── Retrieval stage: same three modes as rag.py's /web, /both, and
        # default (files-only) commands. ──────────────────────────────────
        if payload.mode == "files":
            chunks = retrieve(notebook.id, query)
            if not chunks:
                yield _sse("sources", {"files": [], "web": []})
                yield _sse("error", {"message": "No relevant content found in your documents."})
                return
            yield _sse("sources", {"files": chunks, "web": []})
            prompt = build_prompt(query, chunks)

        elif payload.mode == "web":
            web_results = retrieve_web(query)
            if not web_results:
                yield _sse("sources", {"files": [], "web": []})
                yield _sse("error", {"message": "No web results found."})
                return
            yield _sse("sources", {"files": [], "web": web_results})
            prompt = build_web_prompt(query, web_results)

        else:  # both
            chunks = retrieve(notebook.id, query)
            web_results = retrieve_web(query)
            yield _sse("sources", {"files": chunks, "web": web_results})
            prompt = build_both_prompt(query, chunks, web_results)

        # ── Generation stage: stream tokens as they come from Ollama ──────
        try:
            for token in stream_chat(notebook.llm_model, prompt):
                yield _sse("token", {"content": token})
        except Exception as e:
            yield _sse("error", {"message": str(e)})
            return

        yield _sse("done", {})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
