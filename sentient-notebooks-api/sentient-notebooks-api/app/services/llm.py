from typing import Iterator

from app.core.config import settings


def stream_chat(model: str, prompt: str) -> Iterator[str]:
    """Yields text tokens as they arrive, regardless of provider -- callers
    don't need to know or care which one is active."""
    if settings.llm_provider == "ollama":
        yield from _stream_ollama(model, prompt)
    else:
        yield from _stream_groq(model, prompt)


def _stream_ollama(model: str, prompt: str) -> Iterator[str]:
    """Same streaming call rag.py makes. Needs Ollama running locally --
    fine for your own machine, not realistic on a small/free host."""
    import ollama

    stream = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        stream=True,
    )
    for token in stream:
        content = token.get("message", {}).get("content", "")
        if content:
            yield content


def _stream_groq(model: str, prompt: str) -> Iterator[str]:
    """Groq's free tier -- no server to run, no RAM to provision. Needs
    GROQ_API_KEY set (free signup at console.groq.com, no credit card)."""
    from groq import Groq

    client = Groq(api_key=settings.groq_api_key)
    stream = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        stream=True,
    )
    for chunk in stream:
        content = chunk.choices[0].delta.content
        if content:
            yield content
