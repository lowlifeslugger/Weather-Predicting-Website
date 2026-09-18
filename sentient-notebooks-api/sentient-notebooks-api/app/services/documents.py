import hashlib
import io
from pathlib import Path
from typing import List

from app.core.config import settings

try:
    import pypdf

    PDF_OK = True
except ImportError:
    PDF_OK = False

SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".rst"} | ({".pdf"} if PDF_OK else set())


def is_supported(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS


def extract_text(filename: str, raw: bytes) -> str:
    """Same per-type extraction as rag.py's load_file(), just from bytes
    instead of a path on disk (we're receiving an upload, not scanning a
    folder)."""
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        if not PDF_OK:
            return ""
        reader = pypdf.PdfReader(io.BytesIO(raw))
        pages = [p.extract_text() or "" for p in reader.pages]
        return "\n".join(pages)

    if ext in {".txt", ".md", ".markdown", ".rst"}:
        return raw.decode("utf-8", errors="ignore")

    return ""


def chunk_text(text: str) -> List[str]:
    """Identical logic to rag.py's chunk_text() -- word-count windows with
    overlap, not token-based."""
    words = text.split()
    chunks = []
    i = 0
    step = max(1, settings.chunk_words - settings.chunk_overlap)
    while i < len(words):
        chunk = " ".join(words[i : i + settings.chunk_words])
        if chunk.strip():
            chunks.append(chunk)
        i += step
    return chunks


def file_hash(raw: bytes) -> str:
    return hashlib.md5(raw).hexdigest()
