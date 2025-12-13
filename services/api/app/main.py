import os, glob, uuid
from typing import Dict, Any

from fastapi import FastAPI
from pydantic import BaseModel
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams, PointStruct
import httpx

load_dotenv()

PROVIDER = os.getenv("PROVIDER", "openai").lower()
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-4o-mini")
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
COLLECTION = "docs"

# OpenAI (only if used)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", None)

# Ollama
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "localhost")
OLLAMA_PORT = int(os.getenv("OLLAMA_PORT", "11434"))
OLLAMA_URL = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}"

app = FastAPI(title="Agent PoC RAG API")
qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

class AskBody(BaseModel):
    query: str
    top_k: int = 4

def openai_client():
    from openai import OpenAI
    if OPENAI_BASE_URL:
        return OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
    return OpenAI(api_key=OPENAI_API_KEY)

@app.get("/health")
def health():
    return {"status": "ok", "provider": PROVIDER}

def ensure_collection(vector_dim: int):
    existing = [c.name for c in qdrant.get_collections().collections]
    if COLLECTION not in existing:
        qdrant.recreate_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=vector_dim, distance=Distance.COSINE),
        )

def embed_texts(texts: list[str]) -> list[list[float]]:
    if PROVIDER == "ollama":
        # Ollama embeddings: POST /api/embeddings
        vectors = []
        with httpx.Client(timeout=120) as s:
            for t in texts:
                r = s.post(f"{OLLAMA_URL}/api/embeddings", json={"model": EMBEDDING_MODEL, "prompt": t})
                r.raise_for_status()
                vectors.append(r.json()["embedding"])
        return vectors
    else:
        # OpenAI embeddings
        client = openai_client()
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
        return [d.embedding for d in resp.data]

def chat_with_context(question: str, contexts: list[str]) -> str:
    if PROVIDER == "ollama":
        # Simple chat via Ollama
        prompt = (
            "You are a helpful RAG assistant. Use the provided context to answer succinctly. "
            "If unsure, say so.\n\n"
            f"Question: {question}\n\nContext:\n" + "\n\n---\n\n".join(contexts)
        )
        with httpx.Client(timeout=300) as s:
            r = s.post(f"{OLLAMA_URL}/api/chat", json={
                "model": CHAT_MODEL,
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": prompt}
                ],
                "stream": False,
                "options": {"temperature": 0.2}
            })
            r.raise_for_status()
            data = r.json()
            # new chat API returns {"message":{"content":...}} or ["message": {...}] depending on version
            if "message" in data:
                return data["message"].get("content", "")
            if "choices" in data and data["choices"]:
                return data["choices"][0]["message"]["content"]
            return ""
    else:
        from openai import OpenAI
        client = openai_client()
        system_prompt = "You are a helpful RAG assistant. Use the provided context to answer succinctly. If unsure, say so."
        user_prompt = f"Question: {question}\n\nContext:\n" + "\n\n---\n\n".join(contexts)
        chat = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )
        return chat.choices[0].message.content

@app.post("/ingest")
def ingest() -> Dict[str, Any]:
    doc_dir = "/data/docs"
    files = sorted(glob.glob(os.path.join(doc_dir, "*.txt")))
    if not files:
        return {"ok": False, "message": f"No .txt files found in {doc_dir}"}

    texts = []
    for f in files:
        with open(f, "r", encoding="utf-8") as fh:
            texts.append({"path": f, "text": fh.read()})

    # Determine vector dim from first embedding
    test_vec = embed_texts(["hello world"])[0]
    ensure_collection(len(test_vec))

    points = []
    vecs = embed_texts([t["text"] for t in texts])
    for t, emb in zip(texts, vecs):
        pid = str(uuid.uuid4())
        points.append(PointStruct(id=pid, vector=emb, payload={"path": t["path"], "text": t["text"]}))
    qdrant.upsert(collection_name=COLLECTION, points=points)
    return {"ok": True, "ingested": len(points), "collection": COLLECTION, "provider": PROVIDER}

@app.post("/ask")
def ask(body: AskBody) -> Dict[str, Any]:
    # Embed query
    q_emb = embed_texts([body.query])[0]
    # Search
    search = qdrant.search(collection_name=COLLECTION, query_vector=q_emb, limit=body.top_k)
    contexts, refs = [], []
    for hit in search:
        text = hit.payload.get("text", "")
        path = hit.payload.get("path", "")
        contexts.append(text[:2000])
        refs.append({"path": path, "score": hit.score})
    answer = chat_with_context(body.query, contexts)
    return {"answer": answer, "references": refs, "provider": PROVIDER}
