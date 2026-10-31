"""Embedding 封装（M2）：OpenAI 兼容接口；未配置时 embedding_enabled=False，检索层自动降级。"""
from functools import lru_cache
from typing import Any

from app.config import get_settings


@lru_cache
def get_embedder() -> "Embedder":
    return Embedder()


class Embedder:
    def __init__(self) -> None:
        s = get_settings()
        self.enabled = bool(s.embedding_base_url and s.embedding_model and s.embedding_api_key)
        if not self.enabled:
            return
        from langchain_openai import OpenAIEmbeddings

        self._client = OpenAIEmbeddings(
            model=s.embedding_model,
            base_url=s.embedding_base_url,
            api_key=s.embedding_api_key,
            timeout=30,
        )

    def embed_query(self, text: str) -> list[float]:
        return self._client.embed_query(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._client.embed_documents(texts)
