"""Pinned BGE models loaded one at a time, with CUDA OOM fallback to CPU."""

from __future__ import annotations

import gc
from typing import Sequence

EMBEDDING_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
RERANKER_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"


class BGEModels:
    def __init__(
        self,
        embedding_model: str = "BAAI/bge-m3",
        reranker_model: str = "BAAI/bge-reranker-v2-m3",
        device: str | None = None,
    ):
        self.embedding_model, self.reranker_model, self.device = (
            embedding_model,
            reranker_model,
            device,
        )
        self._tokenizer = None
        self._paths: dict[str, str] = {}

    def _path(self, model: str, revision: str) -> str:
        from huggingface_hub import snapshot_download

        from tracemind.config import LOCAL

        if model not in self._paths:
            self._paths[model] = snapshot_download(
                repo_id=model,
                revision=revision,
                cache_dir=str(LOCAL / "huggingface/hub"),
                ignore_patterns=["onnx/*", "imgs/*", "assets/*", "*.md", ".gitattributes"],
            )
        return self._paths[model]

    def _device(self) -> str:
        import torch

        return self.device or ("cuda" if torch.cuda.is_available() else "cpu")

    def _run(self, kind: str, inputs: Sequence[str], query: str = "") -> list:
        import torch
        from FlagEmbedding import BGEM3FlagModel, FlagReranker

        model_name, revision = (
            (self.embedding_model, EMBEDDING_REVISION)
            if kind == "embed"
            else (self.reranker_model, RERANKER_REVISION)
        )
        path = self._path(model_name, revision)
        device = self._device()
        while True:
            model = None
            try:
                if kind == "embed":
                    model = BGEM3FlagModel(path, use_fp16=device.startswith("cuda"), devices=device)
                    vectors = model.encode(list(inputs), batch_size=12, max_length=8192)[
                        "dense_vecs"
                    ]
                    return [row.tolist() for row in vectors]
                model = FlagReranker(path, use_fp16=device.startswith("cuda"), devices=device)
                scores = model.compute_score(
                    [[query, text] for text in inputs], batch_size=8, max_length=8192
                )
                return (
                    [float(scores)]
                    if isinstance(scores, (int, float))
                    else [float(score) for score in scores]
                )
            except torch.cuda.OutOfMemoryError:
                if not device.startswith("cuda"):
                    raise
                device = "cpu"
            finally:
                del model
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return self._run("embed", texts) if texts else []

    def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        return self._run("rerank", texts, query) if texts else []

    def encode_tokens(self, text: str) -> list[int]:
        if self._tokenizer is None:
            from transformers import AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(
                self._path(self.embedding_model, EMBEDDING_REVISION), local_files_only=True
            )
        return self._tokenizer.encode(text, add_special_tokens=False)

    def decode_tokens(self, tokens: Sequence[int]) -> str:
        if self._tokenizer is None:
            self.encode_tokens("")
        return self._tokenizer.decode(list(tokens), skip_special_tokens=True)
