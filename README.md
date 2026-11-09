# RAG local

Local document ingestion and extractive question answering with FastAPI, LocalStack S3 and Lambda, OpenSearch, and Ollama. No cloud account or hosted inference API is used at runtime.

## Requirements

- Docker Desktop with Docker Compose and at least 6 GB of memory available.
- Windows users should run Compose from PowerShell with Docker Desktop running.
- Internet access is needed on the first startup to download container images, Python packages, and the local Ollama embedding model. Document processing and inference run locally after setup.

The Ollama model `all-minilm` is the local embedding model based on `all-MiniLM-L6-v2` and produces 384-dimensional vectors. It is not a hosted Hugging Face inference service.

## Start the local stack

### Podman on Windows

The Podman machine must be running in rootful mode so LocalStack can use Podman's Docker-compatible API to start its Lambda container. Install the Compose provider inside the Podman VM once:

```powershell
podman machine ssh "sudo dnf install -y podman-compose"
```

Check the current machine and socket in PowerShell. If the machine is not rootful, switch it and enable the API socket:

```powershell
podman machine list
podman info --format json
podman machine stop
podman machine set --rootful
podman machine start
podman machine ssh "sudo systemctl enable --now podman.socket"
podman machine ssh "sudo test -S /run/podman/podman.sock && echo socket-ready"
```

Generate the Lambda deployment package, then run Compose **inside** the Podman VM. This preserves the VM's Unix socket and project mount paths rather than translating them into invalid Windows paths:

```powershell
$project = (Get-Location).Path.Replace('\', '/')
$drive = $project.Substring(0, 1).ToLower()
$guestProject = "/mnt/$drive" + $project.Substring(2)
.\.venv\Scripts\python.exe scripts\package_lambda.py
podman machine ssh "cd '$guestProject' && sudo podman-compose -f docker-compose.yml -f docker-compose.podman.yml up --build -d"
podman machine ssh "sudo podman ps"
```

The first startup downloads the container images and the Ollama model. To inspect services and logs, run:

```powershell
podman machine ssh "cd '$guestProject' && sudo podman-compose -f docker-compose.yml -f docker-compose.podman.yml ps"
podman machine ssh "cd '$guestProject' && sudo podman-compose -f docker-compose.yml -f docker-compose.podman.yml logs --tail=100"
```

### Docker Desktop

Build the Lambda deployment package before starting the stack:

```powershell
.\.venv\Scripts\python.exe scripts\package_lambda.py
```

```powershell
docker compose up --build
```

The first startup builds the application image, starts LocalStack, OpenSearch and Ollama, and downloads `all-minilm`. Wait for the API health check to succeed. The API is at `http://localhost:8000`; interactive API documentation is at `http://localhost:8000/docs`; the optional Streamlit UI is at `http://localhost:8501`.

OpenSearch may require increasing the VM memory allocation. The stack assigns 512 MB to its Java heap.

## Ingest and ask

Upload a PDF and optional JSON metadata:

```powershell
curl.exe -X POST "http://localhost:8000/documents/upload" `
  -F "file=@C:\path\to\manual.pdf" `
  -F 'metadata={"author":"Miguel","department":"AI","language":"es"}'
```

Use the returned `document_id` to invoke the LocalStack Lambda. The Lambda downloads the original from S3, extracts page text, splits it into overlapping chunks, requests local Ollama embeddings, and indexes the chunks and metadata in OpenSearch.

```powershell
curl.exe -X POST "http://localhost:8000/documents/ingest" `
  -H "Content-Type: application/json" `
  -d '{"document_id":"<document_id>"}'
```

Ask a question. The answer is composed from the retrieved source text; no generative language model is called.

```powershell
curl.exe -X POST "http://localhost:8000/ask" `
  -H "Content-Type: application/json" `
  -d '{"question":"¿Cuáles son los requisitos principales?"}'
```

## API endpoints

- `GET /health`: checks LocalStack S3, the deployed Lambda, OpenSearch, and the local Ollama model.
- `POST /documents/upload`: stores a PDF in the `rag-documents` S3 bucket before processing.
- `POST /documents/ingest`: invokes the `document-ingest` Lambda in LocalStack.
- `GET /documents`: lists documents in the bucket.
- `GET /documents/{document_id}`: returns document and upload metadata.
- `GET /documents/{document_id}/chunks`: lists the indexed chunks for a document.
- `POST /retrieve`: returns the most relevant chunks and scores.
- `POST /ask`: returns an extractive answer and the source chunks used.

Both `question` and `pregunta` are accepted in `/ask` and `/retrieve` request bodies. Uploads are limited to PDFs of at most 50 MiB by default.

## Configuration and shutdown

The Compose file configures services to communicate over a private Docker network. Credentials are local dummy values and do not authenticate to AWS. Do not expose these development services to an untrusted network.

To stop services while retaining local documents, model weights, and the Lambda package:

Use the corresponding command for the engine used to start the stack. For Podman:

```powershell
$project = (Get-Location).Path.Replace('\', '/')
$drive = $project.Substring(0, 1).ToLower()
$guestProject = "/mnt/$drive" + $project.Substring(2)
podman machine ssh "cd '$guestProject' && sudo podman-compose -f docker-compose.yml -f docker-compose.podman.yml down"
```

Use `docker compose down` if the stack was started with Docker Desktop. Add `--volumes` to the appropriate command to permanently remove local S3, OpenSearch, and Ollama data.
