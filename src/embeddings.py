import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.config import EMBEDDING_DIMENSION, EMBEDDINGS_MODEL, OLLAMA_BASE_URL


class EmbeddingError(RuntimeError):
    pass


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    request = Request(
        f"{OLLAMA_BASE_URL}/api/embed",
        data=json.dumps({"model": EMBEDDINGS_MODEL, "input": texts}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=180) as response:
            result = json.loads(response.read())
    except (HTTPError, URLError, TimeoutError) as error:
        raise EmbeddingError(f"Ollama embedding request failed: {error}") from error

    embeddings = result.get("embeddings")
    if not isinstance(embeddings, list) or len(embeddings) != len(texts):
        raise EmbeddingError("Ollama returned an unexpected embedding response.")
    if any(not isinstance(vector, list) or len(vector) != EMBEDDING_DIMENSION for vector in embeddings):
        raise EmbeddingError(
            f"Expected {EMBEDDING_DIMENSION}-dimension embeddings from {EMBEDDINGS_MODEL}."
        )
    return embeddings


def embed_text(text: str) -> list[float]:
    return embed_texts([text])[0]


def check_ollama() -> None:
    request = Request(f"{OLLAMA_BASE_URL}/api/tags")
    try:
        with urlopen(request, timeout=10) as response:
            result = json.loads(response.read())
    except (HTTPError, URLError, TimeoutError) as error:
        raise EmbeddingError(f"Ollama health check failed: {error}") from error

    installed_models = {
        model.get("name", "").split(":")[0] for model in result.get("models", [])
    }
    if EMBEDDINGS_MODEL not in installed_models:
        raise EmbeddingError(f"Ollama model '{EMBEDDINGS_MODEL}' is not installed.")
