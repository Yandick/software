from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from ..config import get_settings

INDEX_FORMAT_VERSION = 1
INDEX_BASENAME = "knowledge_embeddings"


class VectorIndexService:
    """Persistent local embedding index for RAG chunks.

    The service keeps a portable numpy vector matrix as the required storage
    layer and writes a FAISS IndexFlatIP sidecar when faiss is available. The
    RAG scorer still uses the vectors directly for hybrid reranking, so FAISS
    remains an optional acceleration path instead of a required deployment.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._active_signature: dict[str, Any] | None = None
        self._active_backend = ""
        self._active_index: Any = None
        self._active_vectors: Any = None

    def load_embeddings(
        self,
        *,
        chunks: list[dict[str, Any]],
        embedding_status: dict[str, Any],
        knowledge_signature: tuple[Any, ...],
    ) -> tuple[list[list[float]] | None, dict[str, Any]]:
        index_dir = self._index_dir()
        vector_path = index_dir / f"{INDEX_BASENAME}.npy"
        meta_path = index_dir / f"{INDEX_BASENAME}_meta.json"
        status = self._base_status(index_dir)
        if not vector_path.exists() or not meta_path.exists():
            return None, {**status, "loaded_from_disk": False, "index_reason": "missing"}

        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return None, {**status, "loaded_from_disk": False, "index_reason": f"metadata_error:{exc.__class__.__name__}"}

        expected_signature = self._signature(chunks, embedding_status, knowledge_signature)
        if metadata.get("format_version") != INDEX_FORMAT_VERSION or metadata.get("signature") != expected_signature:
            return None, {**status, "loaded_from_disk": False, "index_reason": "stale"}

        try:
            import numpy as np

            vectors = np.load(vector_path, allow_pickle=False).astype("float32", copy=False)
        except Exception as exc:
            return None, {**status, "loaded_from_disk": False, "index_reason": f"vector_error:{exc.__class__.__name__}"}

        if len(vectors.shape) != 2 or int(vectors.shape[0]) != len(chunks):
            return None, {**status, "loaded_from_disk": False, "index_reason": "shape_mismatch"}
        backend = self._activate_index(signature=expected_signature, vectors=vectors, faiss_path=index_dir / f"{INDEX_BASENAME}.faiss")
        return (
            vectors.tolist(),
            {
                **status,
                "chunk_count": len(chunks),
                "faiss_path": str(index_dir / f"{INDEX_BASENAME}.faiss"),
                "index_backend": backend,
                "index_path": str(vector_path),
                "index_reason": "matched",
                "loaded_from_disk": True,
                "rebuilt": False,
            },
        )

    def save_embeddings(
        self,
        *,
        chunks: list[dict[str, Any]],
        embeddings: list[list[float]],
        embedding_status: dict[str, Any],
        knowledge_signature: tuple[Any, ...],
    ) -> dict[str, Any]:
        index_dir = self._index_dir()
        index_dir.mkdir(parents=True, exist_ok=True)
        status = self._base_status(index_dir)
        vector_path = index_dir / f"{INDEX_BASENAME}.npy"
        meta_path = index_dir / f"{INDEX_BASENAME}_meta.json"
        faiss_path = index_dir / f"{INDEX_BASENAME}.faiss"

        try:
            import numpy as np

            vectors = np.asarray(embeddings, dtype="float32")
        except Exception as exc:
            return {**status, "loaded_from_disk": False, "rebuilt": True, "index_reason": f"numpy_error:{exc.__class__.__name__}"}

        if len(vectors.shape) != 2 or int(vectors.shape[0]) != len(chunks):
            return {**status, "loaded_from_disk": False, "rebuilt": True, "index_reason": "shape_mismatch"}

        metadata = {
            "backend": {
                "faiss_available": self._faiss_available(),
                "primary": "numpy",
                "requested": self._requested_backend(),
            },
            "chunk_count": len(chunks),
            "chunks": self._chunk_metadata(chunks),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "format_version": INDEX_FORMAT_VERSION,
            "signature": self._signature(chunks, embedding_status, knowledge_signature),
            "vector_dim": int(vectors.shape[1]) if vectors.size else 0,
        }

        try:
            self._atomic_save_numpy(vector_path, vectors)
            backend = self._activate_index(signature=metadata["signature"], vectors=vectors, faiss_path=faiss_path)
            if backend.startswith("faiss"):
                metadata["backend"]["primary"] = backend
            self._atomic_write_json(meta_path, metadata)
        except Exception as exc:
            return {**status, "loaded_from_disk": False, "rebuilt": True, "index_reason": f"save_error:{exc.__class__.__name__}"}

        return {
            **self._base_status(index_dir),
            "chunk_count": len(chunks),
            "faiss_path": str(faiss_path),
            "index_backend": metadata["backend"]["primary"],
            "index_path": str(vector_path),
            "index_reason": "rebuilt",
            "loaded_from_disk": False,
            "rebuilt": True,
        }

    def search(self, query_embedding: list[float], top_k: int) -> list[dict[str, Any]]:
        if not query_embedding or top_k <= 0:
            return []
        with self._lock:
            backend = self._active_backend
            index = self._active_index
            vectors = self._active_vectors
        if not backend:
            return []

        try:
            import numpy as np

            query = np.asarray([query_embedding], dtype="float32")
        except Exception:
            return []

        if backend.startswith("faiss") and index is not None:
            try:
                scores, indexes = index.search(query, int(top_k))
            except Exception:
                return []
            return [
                {"chunk_index": int(index_value), "score": float(score)}
                for index_value, score in zip(indexes[0].tolist(), scores[0].tolist(), strict=False)
                if int(index_value) >= 0
            ]

        if vectors is None:
            return []
        try:
            scores = (vectors @ query[0]).astype("float32", copy=False)
            limit = min(int(top_k), int(scores.shape[0]))
            if limit <= 0:
                return []
            top_indexes = np.argsort(-scores)[:limit]
        except Exception:
            return []
        return [{"chunk_index": int(index_value), "score": float(scores[index_value])} for index_value in top_indexes.tolist()]

    def _index_dir(self) -> Path:
        settings = get_settings()
        path = Path(settings.embedding_index_dir)
        if path.is_absolute():
            return path
        return Path.cwd() / path

    def _requested_backend(self) -> str:
        return str(get_settings().embedding_index_backend or "auto").strip().lower()

    def _base_status(self, index_dir: Path) -> dict[str, Any]:
        return {
            "faiss_available": self._faiss_available(),
            "index_dir": str(index_dir),
            "index_requested_backend": self._requested_backend(),
            "persistent_index": True,
        }

    def _activate_index(self, *, signature: dict[str, Any], vectors: Any, faiss_path: Path) -> str:
        backend = "numpy"
        active_index = None
        if self._requested_backend() != "numpy":
            active_index = self._load_or_build_faiss(faiss_path, vectors)
            if active_index is not None:
                backend = "faiss+numpy"
        with self._lock:
            self._active_signature = signature
            self._active_backend = backend
            self._active_index = active_index
            self._active_vectors = vectors
        return backend

    def _signature(
        self,
        chunks: list[dict[str, Any]],
        embedding_status: dict[str, Any],
        knowledge_signature: tuple[Any, ...],
    ) -> dict[str, Any]:
        return {
            "chunks_fingerprint": self._chunks_fingerprint(chunks),
            "embedding": {
                "dimension": int(embedding_status.get("dimension") or 0),
                "max_length": int(embedding_status.get("max_length") or 0),
                "model_path": str(embedding_status.get("model_path") or ""),
                "provider": str(embedding_status.get("provider") or ""),
            },
            "knowledge": [str(item) if isinstance(item, Path) else item for item in knowledge_signature],
        }

    def _chunks_fingerprint(self, chunks: list[dict[str, Any]]) -> str:
        payload = [
            {
                "chunk_index": int(chunk.get("chunk_index") or 0),
                "knowledge_id": int((chunk.get("knowledge") or {}).get("id") or 0),
                "searchable": str(chunk.get("searchable") or ""),
                "text": str(chunk.get("text") or ""),
            }
            for chunk in chunks
        ]
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _chunk_metadata(self, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        metadata: list[dict[str, Any]] = []
        for chunk in chunks:
            item = chunk.get("knowledge") or {}
            metadata.append(
                {
                    "chunk_index": int(chunk.get("chunk_index") or 0),
                    "knowledge_id": int(item.get("id") or 0),
                    "searchable": str(chunk.get("searchable") or ""),
                    "source_type": str(item.get("source_type") or ""),
                    "status": str(item.get("status") or ""),
                    "tags": str(item.get("tags") or ""),
                    "text": str(chunk.get("text") or ""),
                    "title": str(item.get("title") or ""),
                    "updated_at": str(item.get("updated_at") or ""),
                    "version": int(item.get("version") or 1),
                }
            )
        return metadata

    def _atomic_save_numpy(self, path: Path, vectors: Any) -> None:
        import numpy as np

        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as tmp_file:
            tmp_path = Path(tmp_file.name)
            np.save(tmp_file, vectors)
        tmp_path.replace(path)

    def _atomic_write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False, encoding="utf-8") as tmp_file:
            tmp_path = Path(tmp_file.name)
            json.dump(payload, tmp_file, ensure_ascii=False, indent=2, sort_keys=True)
        tmp_path.replace(path)

    def _faiss_available(self) -> bool:
        try:
            import faiss  # noqa: F401
        except Exception:
            return False
        return True

    def _load_or_build_faiss(self, path: Path, vectors: Any) -> Any:
        requested = self._requested_backend()
        if requested == "numpy":
            return None
        try:
            import faiss
        except Exception:
            return None
        if vectors.size == 0:
            return None
        if path.exists():
            try:
                index = faiss.read_index(str(path))
                if int(index.ntotal) == int(vectors.shape[0]) and int(index.d) == int(vectors.shape[1]):
                    return index
            except Exception:
                pass
        index = faiss.IndexFlatIP(int(vectors.shape[1]))
        index.add(vectors)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as tmp_file:
            tmp_path = Path(tmp_file.name)
        faiss.write_index(index, str(tmp_path))
        tmp_path.replace(path)
        return index


vector_index_service = VectorIndexService()
