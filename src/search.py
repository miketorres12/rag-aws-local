import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.config import EMBEDDING_DIMENSION, OPENSEARCH_INDEX, OPENSEARCH_URL


class OpenSearchError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _request(
    path: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    content_type: str = "application/json",
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{OPENSEARCH_URL}{path}",
        data=data,
        headers={"Content-Type": content_type},
        method=method,
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read()
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise OpenSearchError(
            f"OpenSearch returned HTTP {error.code}: {detail}", status=error.code
        ) from error
    except (URLError, TimeoutError) as error:
        raise OpenSearchError(f"OpenSearch request failed: {error}") from error
    return json.loads(body) if body else {}


def ensure_index() -> None:
    try:
        _request(f"/{OPENSEARCH_INDEX}")
        return
    except OpenSearchError as error:
        if error.status != 404:
            raise

    _request(
        f"/{OPENSEARCH_INDEX}",
        method="PUT",
        payload={
            "settings": {"index": {"knn": True}},
            "mappings": {
                "properties": {
                    "document_id": {"type": "keyword"},
                    "filename": {"type": "keyword"},
                    "chunk_id": {"type": "keyword"},
                    "chunk_position": {"type": "integer"},
                    "chunk_text": {"type": "text"},
                    "embedding": {
                        "type": "knn_vector",
                        "dimension": EMBEDDING_DIMENSION,
                        "method": {
                            "name": "hnsw",
                            "space_type": "cosinesimil",
                            "engine": "nmslib",
                        },
                    },
                    "metadata": {"type": "object", "dynamic": True},
                    "indexed_at": {"type": "date"},
                }
            },
        },
    )


def index_documents(documents: list[dict[str, Any]]) -> None:
    if not documents:
        return
    ensure_index()
    lines: list[str] = []
    for document in documents:
        lines.append(
            json.dumps(
                {"index": {"_index": OPENSEARCH_INDEX, "_id": document["chunk_id"]}},
                ensure_ascii=False,
            )
        )
        lines.append(json.dumps(document, ensure_ascii=False))

    request = Request(
        f"{OPENSEARCH_URL}/_bulk",
        data=("\n".join(lines) + "\n").encode("utf-8"),
        headers={"Content-Type": "application/x-ndjson"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            result = json.loads(response.read())
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise OpenSearchError(
            f"OpenSearch bulk indexing failed with HTTP {error.code}: {detail}",
            status=error.code,
        ) from error
    except (URLError, TimeoutError) as error:
        raise OpenSearchError(f"OpenSearch bulk indexing failed: {error}") from error

    if result.get("errors"):
        failures = [
            item.get("index", {}).get("error")
            for item in result.get("items", [])
            if item.get("index", {}).get("error")
        ]
        raise OpenSearchError(f"OpenSearch rejected one or more chunks: {failures}")


def search_chunks(
    vector: list[float], limit: int = 3, document_id: str | None = None
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"knn": {"embedding": {"vector": vector, "k": limit}}}
    if document_id:
        query = {
            "bool": {
                "must": [query],
                "filter": [{"term": {"document_id": document_id}}],
            }
        }
    response = _request(
        f"/{OPENSEARCH_INDEX}/_search",
        method="POST",
        payload={
            "size": limit,
            "query": query,
            "_source": [
                "document_id",
                "filename",
                "chunk_id",
                "chunk_position",
                "chunk_text",
                "metadata",
                "indexed_at",
            ],
        },
    )
    return response.get("hits", {}).get("hits", [])


def get_document_chunks(document_id: str) -> list[dict[str, Any]]:
    response = _request(
        f"/{OPENSEARCH_INDEX}/_search",
        method="POST",
        payload={
            "size": 10000,
            "query": {"term": {"document_id": document_id}},
            "sort": [{"chunk_position": {"order": "asc"}}],
            "_source": True,
        },
    )
    return [hit.get("_source", {}) for hit in response.get("hits", {}).get("hits", [])]


def check_opensearch() -> None:
    _request("/_cluster/health")
