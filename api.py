import base64
import json
import uuid
from pathlib import PurePosixPath
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from src.config import (
    LAMBDA_FUNCTION_NAME,
    MAX_UPLOAD_BYTES,
    S3_BUCKET_DOCS,
    get_lambda_client,
    get_s3_client,
)
from src.embeddings import check_ollama, embed_text
from src.search import (
    OpenSearchError,
    check_opensearch,
    get_document_chunks,
    search_chunks,
)


class DocumentIngestRequest(BaseModel):
    document_id: str = Field(min_length=1)


class QueryRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    question: str = Field(alias="pregunta", min_length=1, max_length=4000)
    limit: int = Field(default=3, ge=1, le=10)
    document_id: str | None = None


def _ensure_bucket() -> None:
    s3 = get_s3_client()
    existing = {bucket["Name"] for bucket in s3.list_buckets().get("Buckets", [])}
    if S3_BUCKET_DOCS not in existing:
        s3.create_bucket(Bucket=S3_BUCKET_DOCS)


app = FastAPI(
    title="RAG Local API",
    description="Local document ingestion and extractive retrieval API.",
    version="2.0.0",
)


def _decode_metadata(encoded_metadata: str | None) -> dict[str, Any]:
    if not encoded_metadata:
        return {}
    try:
        decoded = base64.urlsafe_b64decode(encoded_metadata.encode("ascii"))
        value = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=500, detail="Stored document metadata is invalid.") from error
    return value if isinstance(value, dict) else {}


def _document_for_key(s3: Any, document_id: str, key: str) -> dict[str, Any]:
    head = s3.head_object(Bucket=S3_BUCKET_DOCS, Key=key)
    object_metadata = head.get("Metadata", {})
    return {
        "document_id": document_id,
        "filename": object_metadata.get("filename", PurePosixPath(key).name),
        "key": key,
        "content_type": head.get("ContentType"),
        "size_bytes": head.get("ContentLength"),
        "uploaded_at": head.get("LastModified").isoformat()
        if head.get("LastModified")
        else None,
        "metadata": _decode_metadata(object_metadata.get("metadata")),
    }


def _find_document(s3: Any, document_id: str) -> dict[str, Any]:
    response = s3.list_objects_v2(
        Bucket=S3_BUCKET_DOCS, Prefix=f"documents/{document_id}/"
    )
    objects = response.get("Contents", [])
    if not objects:
        raise HTTPException(status_code=404, detail="Document not found.")
    return _document_for_key(s3, document_id, objects[0]["Key"])


@app.get("/health")
def health() -> dict[str, Any]:
    checks = {
        "s3": lambda: get_s3_client().list_buckets(),
        "lambda": lambda: get_lambda_client().get_function(
            FunctionName=LAMBDA_FUNCTION_NAME
        ),
        "opensearch": check_opensearch,
        "ollama": check_ollama,
    }
    statuses: dict[str, str] = {}
    details: dict[str, str] = {}
    for component, check in checks.items():
        try:
            check()
            statuses[component] = "ok"
        except Exception as error:
            statuses[component] = "error"
            details[component] = str(error)
    return {
        "status": "ok" if all(value == "ok" for value in statuses.values()) else "degraded",
        **statuses,
        "details": details,
    }


@app.post("/documents/upload", status_code=201)
def upload_document(
    file: UploadFile = File(...),
    metadata: str = Form(default="{}"),
) -> dict[str, Any]:
    filename = PurePosixPath((file.filename or "").replace("\\", "/")).name
    if not filename or PurePosixPath(filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=415, detail="Only PDF documents are supported.")
    try:
        document_metadata = json.loads(metadata)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="metadata must be valid JSON.") from error
    if not isinstance(document_metadata, dict):
        raise HTTPException(status_code=400, detail="metadata must be a JSON object.")

    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded document is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Document exceeds the {MAX_UPLOAD_BYTES}-byte upload limit.",
        )

    document_id = str(uuid.uuid4())
    key = f"documents/{document_id}/{filename}"
    encoded_metadata = base64.urlsafe_b64encode(
        json.dumps(document_metadata, ensure_ascii=True).encode("utf-8")
    ).decode("ascii")
    if len(encoded_metadata) > 1800:
        raise HTTPException(
            status_code=400,
            detail="metadata must not exceed 1800 encoded characters.",
        )
    s3 = get_s3_client()
    _ensure_bucket()
    s3.put_object(
        Bucket=S3_BUCKET_DOCS,
        Key=key,
        Body=content,
        ContentType=file.content_type or "application/pdf",
        Metadata={
            "document_id": document_id,
            "filename": filename,
            "metadata": encoded_metadata,
        },
    )
    return {
        "document_id": document_id,
        "filename": filename,
        "bucket": S3_BUCKET_DOCS,
        "key": key,
        "metadata": document_metadata,
        "status": "uploaded",
    }


@app.post("/documents/ingest")
def ingest_document(request: DocumentIngestRequest) -> dict[str, Any]:
    s3 = get_s3_client()
    document = _find_document(s3, request.document_id)
    event = {
        "document_id": document["document_id"],
        "bucket": S3_BUCKET_DOCS,
        "key": document["key"],
        "filename": document["filename"],
        "metadata": document["metadata"],
    }
    try:
        invocation = get_lambda_client().invoke(
            FunctionName=LAMBDA_FUNCTION_NAME,
            InvocationType="RequestResponse",
            Payload=json.dumps(event).encode("utf-8"),
        )
        response_payload = json.loads(invocation["Payload"].read())
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Local Lambda invocation failed: {error}") from error
    if invocation.get("FunctionError"):
        raise HTTPException(
            status_code=502,
            detail=f"Document ingestion failed: {response_payload}",
        )
    if response_payload.get("statusCode", 200) >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"Document ingestion failed: {response_payload.get('body')}",
        )
    body = response_payload.get("body", "{}")
    if isinstance(body, str):
        body = json.loads(body)
    return {"status": "indexed", **body}


@app.get("/documents")
def list_documents() -> dict[str, list[dict[str, Any]]]:
    s3 = get_s3_client()
    paginator = s3.get_paginator("list_objects_v2")
    documents: dict[str, dict[str, Any]] = {}
    for page in paginator.paginate(Bucket=S3_BUCKET_DOCS, Prefix="documents/"):
        for item in page.get("Contents", []):
            parts = item["Key"].split("/", 2)
            if len(parts) != 3 or parts[1] in documents:
                continue
            documents[parts[1]] = _document_for_key(s3, parts[1], item["Key"])
    return {"documents": list(documents.values())}


@app.get("/documents/{document_id}")
def get_document(document_id: str) -> dict[str, Any]:
    return _find_document(get_s3_client(), document_id)


@app.get("/documents/{document_id}/chunks")
def list_document_chunks(document_id: str) -> dict[str, Any]:
    _find_document(get_s3_client(), document_id)
    try:
        chunks = get_document_chunks(document_id)
    except OpenSearchError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {"document_id": document_id, "chunks": chunks}


def _retrieve(question: str, limit: int, document_id: str | None = None) -> list[dict[str, Any]]:
    try:
        vector = embed_text(question)
        hits = search_chunks(vector, limit=limit, document_id=document_id)
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Local retrieval failed: {error}") from error

    return [
        {
            **hit.get("_source", {}),
            "score": hit.get("_score"),
        }
        for hit in hits
    ]


@app.post("/retrieve")
def retrieve(request: QueryRequest) -> dict[str, Any]:
    return {
        "question": request.question,
        "results": _retrieve(request.question, request.limit, request.document_id),
    }


@app.post("/ask")
def ask(request: QueryRequest) -> dict[str, Any]:
    sources = _retrieve(request.question, request.limit, request.document_id)
    answer = (
        "\n\n".join(source["chunk_text"] for source in sources if source.get("chunk_text"))
        or "No se encontraron fragmentos relevantes para responder."
    )
    return {
        "pregunta": request.question,
        "respuesta": answer,
        "fuentes": [
            {
                "document_id": source.get("document_id"),
                "filename": source.get("filename"),
                "chunk_id": source.get("chunk_id"),
                "chunk_position": source.get("chunk_position"),
                "score": source.get("score"),
                "chunk_text": source.get("chunk_text"),
                "metadata": source.get("metadata", {}),
            }
            for source in sources
        ],
    }


@app.get("/status", include_in_schema=False)
def legacy_status() -> dict[str, Any]:
    return health()


@app.post("/preguntar", include_in_schema=False)
def legacy_question(request: QueryRequest) -> dict[str, Any]:
    return ask(request)
