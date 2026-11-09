import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
from pypdf import PdfReader

from src.config import (
    AWS_REGION,
    AWS_ACCESS_KEY,
    AWS_SECRET_KEY,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    S3_BUCKET_DOCS,
)
from src.embeddings import embed_texts
from src.search import index_documents


def split_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("Chunk size must be positive and overlap smaller than chunk size.")

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            boundary = text.rfind(" ", start, end)
            if boundary > start:
                end = boundary
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    document_id = event.get("document_id")
    bucket = event.get("bucket", S3_BUCKET_DOCS)
    object_key = event.get("key")
    filename = event.get("filename")
    metadata = event.get("metadata", {})

    if not document_id or not object_key or not filename:
        raise ValueError("document_id, key, and filename are required.")
    if Path(filename).suffix.lower() != ".pdf":
        raise ValueError("Only PDF documents are supported.")

    s3 = boto3.client(
        "s3",
        endpoint_url=os.getenv("S3_ENDPOINT", "http://localstack:4566"),
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
    )
    with tempfile.TemporaryDirectory() as temporary_directory:
        pdf_path = Path(temporary_directory) / "document.pdf"
        s3.download_file(bucket, object_key, str(pdf_path))
        reader = PdfReader(str(pdf_path))
        chunks: list[dict[str, Any]] = []
        for page_number, page in enumerate(reader.pages, start=1):
            page_text = page.extract_text() or ""
            for text in split_text(page_text):
                chunks.append({"text": text, "page_number": page_number})

    if not chunks:
        raise ValueError("No extractable text was found in the PDF.")

    vectors: list[list[float]] = []
    for batch_start in range(0, len(chunks), 32):
        batch = chunks[batch_start : batch_start + 32]
        vectors.extend(embed_texts([chunk["text"] for chunk in batch]))

    indexed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    documents = [
        {
            "document_id": document_id,
            "filename": filename,
            "chunk_id": f"{document_id}_chunk_{position:04d}",
            "chunk_position": position,
            "chunk_text": chunk["text"],
            "embedding": vectors[position - 1],
            "metadata": {**metadata, "page_number": chunk["page_number"]},
            "indexed_at": indexed_at,
        }
        for position, chunk in enumerate(chunks, start=1)
    ]
    index_documents(documents)
    return {
        "statusCode": 200,
        "body": json.dumps(
            {"document_id": document_id, "indexed_chunks": len(documents)},
            ensure_ascii=False,
        ),
    }
