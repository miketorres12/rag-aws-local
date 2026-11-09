import base64
import io
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api import app
from src.lambda_func.handler import split_text


class ChunkingTests(unittest.TestCase):
    def test_chunks_have_configured_overlap(self) -> None:
        chunks = split_text("a" * 2500, chunk_size=1000, overlap=200)

        self.assertEqual([len(chunk) for chunk in chunks], [1000, 1000, 900])
        self.assertEqual(chunks[0][-200:], chunks[1][:200])
        self.assertEqual(chunks[1][-200:], chunks[2][:200])

    def test_invalid_overlap_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            split_text("text", chunk_size=10, overlap=10)


class AskEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bucket_patch = patch("api._ensure_bucket")
        self.bucket_patch.start()
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.bucket_patch.stop()

    @patch("api._retrieve")
    def test_ask_returns_exact_retrieved_text_and_sources(self, retrieve: object) -> None:
        retrieve.return_value = [  # type: ignore[attr-defined]
            {
                "document_id": "doc-1",
                "filename": "manual.pdf",
                "chunk_id": "doc-1_chunk_0001",
                "chunk_position": 1,
                "chunk_text": "El manual establece el requisito.",
                "metadata": {"page_number": 2},
                "score": 0.9,
            }
        ]

        response = self.client.post("/ask", json={"question": "¿Qué establece?"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["respuesta"], "El manual establece el requisito.")
        self.assertEqual(response.json()["fuentes"][0]["filename"], "manual.pdf")
        self.assertEqual(response.json()["fuentes"][0]["chunk_id"], "doc-1_chunk_0001")

    def test_question_in_spanish_is_accepted(self) -> None:
        with patch("api._retrieve", return_value=[]):
            response = self.client.post("/ask", json={"pregunta": "Consulta"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["respuesta"],
            "No se encontraron fragmentos relevantes para responder.",
        )

    def test_health_reports_required_local_services(self) -> None:
        with (
            patch("api.get_s3_client") as get_s3,
            patch("api.get_lambda_client") as get_lambda,
            patch("api.check_opensearch"),
            patch("api.check_ollama"),
        ):
            get_s3.return_value.list_buckets.return_value = {"Buckets": []}
            get_lambda.return_value.get_function.return_value = {"Configuration": {}}
            response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["s3"], "ok")
        self.assertEqual(response.json()["lambda"], "ok")
        self.assertEqual(response.json()["opensearch"], "ok")
        self.assertEqual(response.json()["ollama"], "ok")

    @patch("api.search_chunks")
    @patch("api.embed_text", return_value=[0.1] * 384)
    def test_retrieve_returns_top_source_chunks(self, embed: object, search: object) -> None:
        search.return_value = [  # type: ignore[attr-defined]
            {
                "_score": 0.8,
                "_source": {
                    "document_id": "doc-1",
                    "chunk_id": "doc-1_chunk_0001",
                    "chunk_text": "Fragmento relevante",
                },
            }
        ]

        response = self.client.post("/retrieve", json={"question": "consulta"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["chunk_text"], "Fragmento relevante")
        self.assertEqual(response.json()["results"][0]["score"], 0.8)
        embed.assert_called_once_with("consulta")  # type: ignore[attr-defined]

    @patch("api.get_s3_client")
    def test_upload_stores_pdf_in_s3_before_returning_document_id(self, get_s3: object) -> None:
        response = self.client.post(
            "/documents/upload",
            files={"file": ("manual.pdf", b"%PDF-1.4 local test", "application/pdf")},
            data={"metadata": '{"author":"Miguel"}'},
        )

        self.assertEqual(response.status_code, 201)
        document_id = response.json()["document_id"]
        get_s3.return_value.put_object.assert_called_once()  # type: ignore[attr-defined]
        upload = get_s3.return_value.put_object.call_args.kwargs  # type: ignore[attr-defined]
        self.assertEqual(upload["Bucket"], "rag-documents")
        self.assertEqual(upload["Metadata"]["document_id"], document_id)

    @patch("api.get_lambda_client")
    @patch("api.get_s3_client")
    def test_ingest_invokes_local_lambda_with_uploaded_s3_object(
        self, get_s3: object, get_lambda: object
    ) -> None:
        document_id = "doc-1"
        get_s3.return_value.list_objects_v2.return_value = {  # type: ignore[attr-defined]
            "Contents": [{"Key": "documents/doc-1/manual.pdf"}]
        }
        get_s3.return_value.head_object.return_value = {  # type: ignore[attr-defined]
            "Metadata": {
                "filename": "manual.pdf",
                "metadata": base64.urlsafe_b64encode(b'{"author":"Miguel"}').decode(),
            },
            "ContentLength": 20,
        }
        get_lambda.return_value.invoke.return_value = {  # type: ignore[attr-defined]
            "Payload": io.BytesIO(
                json.dumps(
                    {
                        "statusCode": 200,
                        "body": json.dumps({"document_id": document_id, "indexed_chunks": 4}),
                    }
                ).encode()
            )
        }

        response = self.client.post(
            "/documents/ingest", json={"document_id": document_id}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["indexed_chunks"], 4)
        event = json.loads(
            get_lambda.return_value.invoke.call_args.kwargs["Payload"]  # type: ignore[attr-defined]
        )
        self.assertEqual(event["key"], "documents/doc-1/manual.pdf")
        self.assertEqual(event["metadata"], {"author": "Miguel"})


if __name__ == "__main__":
    unittest.main()
