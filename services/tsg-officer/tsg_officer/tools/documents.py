from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Tuple

from tsg_officer.state.models import Document


def add_document(documents: List[Document], *, name: str, text: str, mime_type: str = "text/plain") -> Tuple[List[Document], str]:
    doc_id = str(uuid.uuid4())
    doc: Document = {
        "doc_id": doc_id,
        "name": name,
        "mime_type": mime_type,
        "text": text,
    }
    return documents + [doc], doc_id


def concat_documents(documents: List[Document], max_chars: int = 40_000) -> str:
    """Concatenate docs for simple demo prompting (replace with real RAG in production)."""
    chunks: List[str] = []
    for d in documents:
        name = d.get("name", "document")
        text = d.get("text", "")
        if not text:
            continue
        chunks.append(f"### {name}\n{text}\n")
        if sum(len(c) for c in chunks) >= max_chars:
            break
    joined = "\n".join(chunks)
    return joined[:max_chars]
