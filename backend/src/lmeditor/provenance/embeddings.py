"""Semantic embeddings for the dense retrieval index.

``sentence-transformers`` is a declared dependency of this project and the
``sentence-transformers/all-MiniLM-L6-v2`` model is present in the local
Hugging Face cache, yet the dense index never used it: with no embedder
supplied it fell back to a SHA-256 term-frequency hash, which cannot tell
"model" from "modelling" and puts every passage with the same words in the
same place.

This module provides the missing piece: a lazy, failure-tolerant wrapper
around ``SentenceTransformer`` that

* never touches the network at import time -- the model is loaded on first
  use, not when the module is imported;
* loads the model from the local cache (``local_files_only=True``), so it
  works on a machine with no network at all;
* degrades to nothing rather than raising, so the caller can fall back to the
  deterministic hash vectorizer and *say so*.

The degradation is reported, never silent: :attr:`SentenceTransformerEmbedder.name`
names the embedder in use, and the dense index exposes it as
``embedder_name``.
"""

from __future__ import annotations

import logging
from typing import Any, Final, Optional, Sequence

logger = logging.getLogger(__name__)

#: The sentence-transformers model the dense index uses. It is small
#: (about 90 MB), fast on CPU, and produces 384-dimensional embeddings.
SEMANTIC_MODEL_NAME: Final[str] = "sentence-transformers/all-MiniLM-L6-v2"

#: Models already loaded in this process, keyed by name. Loading a
#: sentence-transformers model costs seconds and hundreds of megabytes, and
#: every index in the process wants the same one, so the model is shared.
_MODEL_CACHE: dict[str, Any] = {}


class EmbeddingUnavailableError(RuntimeError):
    """Raised when the semantic embedder cannot produce a vector."""


class SentenceTransformerEmbedder:
    """Lazy, offline-tolerant wrapper around a ``SentenceTransformer`` model.

    The model is loaded on the first call to :meth:`embed` and the load
    happens at most once: a failed load is remembered, so a machine without
    the model cached does not pay the load cost for every chunk.
    """

    def __init__(
        self,
        model_name: str = SEMANTIC_MODEL_NAME,
        *,
        allow_download: bool = False,
    ) -> None:
        self.model_name = model_name
        self.allow_download = allow_download
        self._model: Any | None = None
        self._load_error: Optional[str] = None
        self._load_attempted = False

    @property
    def name(self) -> str:
        """Return the name of the model this embedder uses."""
        return self.model_name

    @property
    def load_error(self) -> Optional[str]:
        """Return why the model could not be loaded, or ``None``."""
        return self._load_error

    def _load(self) -> Any | None:
        """Return the loaded model, or ``None`` when it cannot be loaded."""
        if self._model is not None:
            return self._model
        if self._load_attempted:
            return None
        self._load_attempted = True
        cached = _MODEL_CACHE.get(self.model_name)
        if cached is not None:
            self._model = cached
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:
            self._load_error = f"sentence-transformers is not importable: {exc}"
            logger.warning("%s", self._load_error)
            return None

        try:
            # ``local_files_only`` keeps the load offline: the model is read
            # from the local Hugging Face cache and no request is made.
            model = SentenceTransformer(self.model_name, local_files_only=True)
        except TypeError:
            # Older sentence-transformers releases have no ``local_files_only``
            # keyword; they are offline anyway when the model is cached.
            try:
                model = SentenceTransformer(self.model_name)
            except Exception as exc:
                self._load_error = f"could not load {self.model_name!r}: {exc}"
                logger.warning("%s", self._load_error)
                return None
        except Exception as exc:
            if not self.allow_download:
                self._load_error = (
                    f"could not load {self.model_name!r} from the local cache: {exc}"
                )
                logger.warning("%s", self._load_error)
                return None
            try:
                model = SentenceTransformer(self.model_name)
            except Exception as download_exc:
                self._load_error = (
                    f"could not load {self.model_name!r}: {download_exc}"
                )
                logger.warning("%s", self._load_error)
                return None
        _MODEL_CACHE[self.model_name] = model
        self._model = model
        return self._model

    def is_available(self) -> bool:
        """Return True when the model can be loaded."""
        return self._load() is not None

    def embed(self, text: str) -> list[float]:
        """Return the embedding of ``text`` as a list of floats."""
        model = self._load()
        if model is None:
            raise EmbeddingUnavailableError(
                self._load_error or f"{self.model_name!r} is not available"
            )
        vector = model.encode(text)
        return [float(value) for value in vector]

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        """Return the embeddings of ``texts`` as a list of vectors."""
        model = self._load()
        if model is None:
            raise EmbeddingUnavailableError(
                self._load_error or f"{self.model_name!r} is not available"
            )
        vectors = model.encode(list(texts))
        return [[float(value) for value in vector] for vector in vectors]


__all__ = [
    "SEMANTIC_MODEL_NAME",
    "EmbeddingUnavailableError",
    "SentenceTransformerEmbedder",
]
