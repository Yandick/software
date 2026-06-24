from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any

from ..config import get_settings


class EmbeddingService:
    """Local Qwen3 embedding model adapter.

    The implementation follows the Qwen3-Embedding model card: left padding,
    last-token pooling, query-side instruction, and L2 normalization.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._loaded_key: tuple[str, str, int, int] | None = None
        self._model: Any = None
        self._tokenizer: Any = None
        self._device = "cpu"

    def status(self) -> dict[str, Any]:
        settings = get_settings()
        path = self._resolve_path(settings.embedding_model_path)
        return {
            "batch_size": settings.embedding_batch_size,
            "device": settings.embedding_device,
            "dimension": settings.embedding_dimension,
            "enabled": settings.enable_embedding_rag,
            "loaded": self._model is not None,
            "max_length": settings.embedding_max_length,
            "model_path": str(path),
            "model_path_exists": path.exists(),
            "provider": "local_qwen3_embedding_transformers",
        }

    def embed_query(self, query: str) -> list[float]:
        settings = get_settings()
        instructed = f"Instruct: {settings.embedding_query_instruction}\nQuery:{query}"
        return self.embed_documents([instructed])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        settings = get_settings()
        if not settings.enable_embedding_rag:
            raise RuntimeError("embedding_rag_disabled")
        model, tokenizer, device = self._load()

        import torch
        import torch.nn.functional as F

        vectors: list[list[float]] = []
        batch_size = max(1, int(settings.embedding_batch_size))
        with self._lock:
            for start in range(0, len(texts), batch_size):
                batch = texts[start : start + batch_size]
                encoded = tokenizer(
                    batch,
                    padding=True,
                    truncation=True,
                    max_length=settings.embedding_max_length,
                    return_tensors="pt",
                )
                encoded = {key: value.to(device) for key, value in encoded.items()}
                with torch.inference_mode():
                    outputs = model(**encoded)
                    embeddings = self._last_token_pool(outputs.last_hidden_state, encoded["attention_mask"])
                    dim = max(1, min(int(settings.embedding_dimension), int(embeddings.shape[-1])))
                    embeddings = embeddings[:, :dim]
                    embeddings = F.normalize(embeddings, p=2, dim=1)
                vectors.extend(embeddings.detach().cpu().float().tolist())
        return vectors

    def _load(self) -> tuple[Any, Any, str]:
        settings = get_settings()
        path = self._resolve_path(settings.embedding_model_path)
        key = (
            str(path),
            settings.embedding_device,
            int(settings.embedding_max_length),
            int(settings.embedding_dimension),
        )
        with self._lock:
            if self._model is not None and self._tokenizer is not None and self._loaded_key == key:
                return self._model, self._tokenizer, self._device
            if not path.exists():
                raise RuntimeError(f"embedding_model_path_missing:{path}")

            import torch
            from transformers import AutoModel, AutoTokenizer

            device = self._resolve_device(settings.embedding_device, torch)
            tokenizer = AutoTokenizer.from_pretrained(str(path), padding_side="left", local_files_only=True)
            kwargs: dict[str, Any] = {"local_files_only": True}
            if device.startswith("cuda"):
                kwargs["torch_dtype"] = torch.bfloat16
            model = AutoModel.from_pretrained(str(path), **kwargs)
            model.to(device)
            model.eval()

            self._loaded_key = key
            self._model = model
            self._tokenizer = tokenizer
            self._device = device
            return model, tokenizer, device

    def _resolve_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return Path.cwd() / path

    def _resolve_device(self, value: str, torch: Any) -> str:
        requested = str(value or "auto").strip().lower()
        if requested == "auto":
            return "cuda" if torch.cuda.is_available() else "cpu"
        return requested

    def _last_token_pool(self, last_hidden_states: Any, attention_mask: Any) -> Any:
        import torch

        left_padding = bool((attention_mask[:, -1].sum() == attention_mask.shape[0]).item())
        if left_padding:
            return last_hidden_states[:, -1]
        sequence_lengths = attention_mask.sum(dim=1) - 1
        batch_size = last_hidden_states.shape[0]
        return last_hidden_states[torch.arange(batch_size, device=last_hidden_states.device), sequence_lengths]


embedding_service = EmbeddingService()
