import json
import uuid
from pathlib import Path
from typing import Any

from src.config import DATA_DIR, LAMBDA_FUNCTION_NAME, S3_BUCKET_DOCS, get_lambda_client, get_s3_client


def upload_raw_pdf(filename: str) -> str:
    file_path = DATA_DIR / filename
    if not file_path.is_file() or file_path.suffix.lower() != ".pdf":
        raise FileNotFoundError(f"PDF not found in the raw data directory: {file_path}")

    document_id = str(uuid.uuid4())
    key = f"documents/{document_id}/{Path(filename).name}"
    get_s3_client().upload_file(str(file_path), S3_BUCKET_DOCS, key)
    return document_id


def process_and_ingest_pdf(document_id: str) -> dict[str, Any]:
    s3 = get_s3_client()
    prefix = f"documents/{document_id}/"
    response = s3.list_objects_v2(Bucket=S3_BUCKET_DOCS, Prefix=prefix)
    objects = response.get("Contents", [])
    if not objects:
        raise FileNotFoundError(f"Document {document_id} was not found in the S3 bucket.")

    key = objects[0]["Key"]
    filename = Path(key).name
    result = get_lambda_client().invoke(
        FunctionName=LAMBDA_FUNCTION_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps(
            {
                "document_id": document_id,
                "bucket": S3_BUCKET_DOCS,
                "key": key,
                "filename": filename,
                "metadata": {},
            }
        ).encode("utf-8"),
    )
    payload = json.loads(result["Payload"].read())
    if result.get("FunctionError"):
        raise RuntimeError(f"Document ingestion failed: {payload}")
    body = payload.get("body", "{}")
    return json.loads(body) if isinstance(body, str) else body


if __name__ == "__main__":
    test_pdf = "Reglamento_general_de_la_ley_de_transito_y_transporte_edomex.pdf"
    uploaded_id = upload_raw_pdf(test_pdf)
    print(process_and_ingest_pdf(uploaded_id))
