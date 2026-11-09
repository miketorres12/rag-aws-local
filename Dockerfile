FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY api.py app.py setup_buckets.py ./
COPY src ./src
COPY scripts/package_lambda.py ./scripts/package_lambda.py
RUN python scripts/package_lambda.py

EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
