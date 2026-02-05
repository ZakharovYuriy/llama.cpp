import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np


MANIFEST_NAME = "manifest.json"
INDEX_NAME = "faiss.index"
META_NAME = "meta.jsonl"
RAG_VERSION = 1


@dataclass(frozen=True)
class RagConfig:
    enabled: bool
    docs_dir: Path
    emb_model_path: Path
    index_dir: Path
    top_k: int
    chunk_size: int
    chunk_overlap: int

    def resolved(self) -> "RagConfig":
        return RagConfig(
            enabled=self.enabled,
            docs_dir=self.docs_dir.resolve(),
            emb_model_path=self.emb_model_path.resolve(),
            index_dir=self.index_dir.resolve(),
            top_k=self.top_k,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )


class RagEngine:
    def __init__(self, config: RagConfig) -> None:
        self.config = config.resolved()
        self._model = None
        self._index = None
        self._meta: List[Dict] = []
        self._ready = False
        self._init_lock = threading.Lock()
        self._encode_lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def stats(self) -> Dict[str, int]:
        chunks = len(self._meta)
        index_size = int(self._index.ntotal) if self._index is not None else 0
        return {"chunks": chunks, "index_size": index_size}

    def ensure_ready(self) -> None:
        if not self.config.enabled:
            return
        if self._ready:
            return
        with self._init_lock:
            if self._ready:
                return
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(str(self.config.emb_model_path))
            if self._index is None or not self._meta:
                self._index, self._meta = load_or_build_index(self._model, self.config)
            self._ready = True

    def retrieve(self, query: str, top_k: Optional[int] = None) -> List[Dict]:
        if not self.config.enabled:
            return []
        if not query or not query.strip():
            return []
        self.ensure_ready()
        if self._index is None or self._index.ntotal == 0:
            return []

        prompt = f"query: {query.strip()}"
        start = time.perf_counter()
        with self._encode_lock:
            q_emb = self._model.encode([prompt], normalize_embeddings=True)
        q_emb = np.asarray(q_emb, dtype="float32")
        k = top_k or self.config.top_k
        scores, ids = self._index.search(q_emb, k)

        out: List[Dict] = []
        for score, idx in zip(scores[0], ids[0]):
            if idx < 0 or idx >= len(self._meta):
                continue
            item = dict(self._meta[idx])
            item["score"] = float(score)
            out.append(item)
        elapsed_ms = (time.perf_counter() - start) * 1000
        logging.info(
            "RAG retrieve: query_chars=%s top_k=%s hits=%s time_ms=%.1f",
            len(query),
            k,
            len(out),
            elapsed_ms,
        )
        return out


def load_or_build_index(model, config: RagConfig):
    index_dir = config.index_dir
    index_path = index_dir / INDEX_NAME
    meta_path = index_dir / META_NAME
    manifest_path = index_dir / MANIFEST_NAME

    index_dir.mkdir(parents=True, exist_ok=True)

    manifest = _load_manifest(manifest_path)
    if index_path.exists() and meta_path.exists() and _manifest_matches(manifest, config):
        index, meta = load_index(index_path, meta_path)
        logging.info("RAG index loaded: %s chunks", len(meta))
        return index, meta

    if index_path.exists() or meta_path.exists():
        logging.info("RAG index rebuild: manifest mismatch or missing")

    logging.info("Building RAG index from %s", config.docs_dir)
    index, meta = build_index(model, config)
    save_index(index, meta, index_path, meta_path)
    save_manifest(manifest_path, config, len(meta))
    return index, meta


def load_index(index_path: Path, meta_path: Path):
    import faiss

    index = faiss.read_index(str(index_path))
    meta: List[Dict] = []
    with meta_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            meta.append(json.loads(line))
    return index, meta


def save_index(index, meta: List[Dict], index_path: Path, meta_path: Path) -> None:
    import faiss

    faiss.write_index(index, str(index_path))
    with meta_path.open("w", encoding="utf-8") as handle:
        for item in meta:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def save_manifest(path: Path, config: RagConfig, chunk_count: int) -> None:
    data = {
        "rag_version": RAG_VERSION,
        "docs_dir": str(config.docs_dir),
        "emb_model_path": str(config.emb_model_path),
        "chunk_size": config.chunk_size,
        "chunk_overlap": config.chunk_overlap,
        "chunk_count": chunk_count,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_manifest(path: Path) -> Optional[Dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _manifest_matches(manifest: Optional[Dict], config: RagConfig) -> bool:
    if not manifest:
        return False
    return (
        manifest.get("rag_version") == RAG_VERSION
        and Path(manifest.get("docs_dir", "")).resolve() == config.docs_dir
        and Path(manifest.get("emb_model_path", "")).resolve() == config.emb_model_path
        and manifest.get("chunk_size") == config.chunk_size
        and manifest.get("chunk_overlap") == config.chunk_overlap
    )


def build_index(model, config: RagConfig):
    import faiss

    start = time.perf_counter()
    dim = model.get_sentence_embedding_dimension()
    index = faiss.IndexFlatIP(dim)

    meta: List[Dict] = []
    batch: List[str] = []
    batch_size = 64
    total_chars = 0
    paths = _list_doc_paths(config.docs_dir)
    logging.info("RAG ingest: %s files", len(paths))

    for item in iter_chunks(config.docs_dir, config.chunk_size, config.chunk_overlap, paths=paths):
        meta.append(item)
        batch.append(f"passage: {item['text']}")
        total_chars += len(item.get("text", ""))
        if len(batch) >= batch_size:
            _add_batch(index, model, batch)
            batch.clear()

    if batch:
        _add_batch(index, model, batch)

    elapsed = time.perf_counter() - start
    logging.info(
        "RAG index ready: %s chunks, %s chars, dim=%s, time_s=%.2f",
        len(meta),
        total_chars,
        dim,
        elapsed,
    )
    return index, meta


def _add_batch(index, model, texts: List[str]) -> None:
    embeddings = model.encode(texts, normalize_embeddings=True)
    embeddings = np.asarray(embeddings, dtype="float32")
    index.add(embeddings)


def _list_doc_paths(docs_dir: Path) -> List[Path]:
    paths: List[Path] = []
    for path in sorted(docs_dir.rglob("*")):
        if path.is_dir():
            continue
        if path.suffix.lower() not in {".txt", ".md"}:
            continue
        paths.append(path)
    return paths


def iter_chunks(
    docs_dir: Path,
    chunk_size: int,
    chunk_overlap: int,
    paths: Optional[List[Path]] = None,
) -> Iterable[Dict]:
    if not docs_dir.exists():
        raise FileNotFoundError(f"docs-dir not found: {docs_dir}")

    size = max(1, chunk_size)
    overlap = max(0, min(chunk_overlap, size - 1))
    step = max(1, size - overlap)

    if paths is None:
        paths = _list_doc_paths(docs_dir)

    for path in paths:
        text = _read_text(path)
        if not text:
            continue

        source_path = str(path.relative_to(docs_dir))
        for chunk_id, offset, chunk in _chunk_text(text, size, step):
            yield {
                "source_path": source_path,
                "chunk_id": chunk_id,
                "offset": offset,
                "text": chunk,
            }


def _read_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def _chunk_text(text: str, size: int, step: int) -> Iterable[Tuple[int, int, str]]:
    cursor = 0
    chunk_id = 0
    length = len(text)
    while cursor < length:
        end = min(length, cursor + size)
        chunk = text[cursor:end].strip()
        if chunk:
            yield chunk_id, cursor, chunk
            chunk_id += 1
        if end >= length:
            break
        cursor += step
