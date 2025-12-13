import os
import glob
import uuid
from typing import List, Dict, Any

from fastapi import FastAPI
from pydantic import BaseModel
from dotenv import load_dotenv

from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams, PointStruct

from openai import OpenAI

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-4o-mini")
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
COLLECTION = "docs"

client = OpenAI(api_key=OPENAI_API_KEY)
qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

app = FastAPI(title="Agent PoC RAG API")

class AskBody(BaseModel):
    query: str
    top_k: int = 4

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/ingest")
def ingest() -> Dict[str, Any]:
    """
    Read .txt files in /data/docs, embed, upsert to Qdrant.
    Collection dimension is auto-inferred from first embedding length.
    """
    doc_dir = "/data/docs"
    files = sorted(glob.glob(os.path.join(doc_dir, "*.txt")))
    if not files:
        return {"ok": False, "message": f"No .txt files found in {doc_dir}"}

    texts = []
    for f in files:
        with open(f, "r", encoding="utf-8") as fh:
            texts.append({"path": f, "text": fh.read()})

    # Make an embedding to determine vector size
    test_emb = client.embeddings.create(model=EMBEDDING_MODEL, input="hello world").data[0].embedding
    dim = len(test_emb)

    # Create collection if missing
    existing = [c.name for c in qdrant.get_collections().collections]
    if COLLECTION not in existing:
        qdrant.recreate_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )

    points = []
    for t in texts:
        emb = client.embeddings.create(model=EMBEDDING_MODEL, input=t["text"]).data[0].embedding
        pid = str(uuid.uuid4())
        points.append(PointStruct(id=pid, vector=emb, payload={"path": t["path"], "text": t["text"]}))

    qdrant.upsert(collection_name=COLLECTION, points=points)

    return {"ok": True, "ingested": len(points), "collection": COLLECTION}

@app.post("/ask")
def ask(body: AskBody) -> Dict[str, Any]:
    # Embed query
    q_emb = client.embeddings.create(model=EMBEDDING_MODEL, input=body.query).data[0].embedding

    # Search
    search = qdrant.search(
        collection_name=COLLECTION,
        query_vector=q_emb,
        limit=body.top_k
    )

    contexts = []
    refs = []
    for hit in search:
        text = hit.payload.get("text", "")
        path = hit.payload.get("path", "")
        contexts.append(text[:2000])  # clamp context length
        refs.append({"path": path, "score": hit.score})

    system_prompt = "You are a helpful RAG assistant. Use the provided context to answer succinctly. If unsure, say so."
    user_prompt = f"Question: {body.query}\n\nContext:\n" + "\n\n---\n\n".join(contexts)

    chat = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )

    answer = chat.choices[0].message.content
    return {"answer": answer, "references": refs}
