#!/bin/bash
set -euo pipefail

FUNCTION_NAME="document-ingest"
ROLE_NAME="rag-local-lambda-role"
ROLE_ARN="arn:aws:iam::000000000000:role/${ROLE_NAME}"
ZIP_FILE="/opt/lambda/ingest.zip"

awslocal iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1 || \
  awslocal iam create-role \
    --role-name "${ROLE_NAME}" \
    --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
    >/dev/null

if awslocal lambda get-function --function-name "${FUNCTION_NAME}" >/dev/null 2>&1; then
  awslocal lambda update-function-code \
    --function-name "${FUNCTION_NAME}" \
    --zip-file "fileb://${ZIP_FILE}" \
    >/dev/null
else
  awslocal lambda create-function \
    --function-name "${FUNCTION_NAME}" \
    --runtime python3.11 \
    --handler handler.lambda_handler \
    --role "${ROLE_ARN}" \
    --timeout 300 \
    --memory-size 1024 \
    --zip-file "fileb://${ZIP_FILE}" \
    --environment \
    'Variables={S3_ENDPOINT=http://localstack:4566,OPENSEARCH_URL=http://opensearch:9200,OLLAMA_BASE_URL=http://ollama:11434,S3_BUCKET_DOCS=rag-documents,EMBEDDINGS_MODEL=all-minilm,AWS_ACCESS_KEY_ID=test,AWS_SECRET_ACCESS_KEY=test,AWS_REGION=us-east-1}' \
    >/dev/null
fi

echo "Local Lambda function ${FUNCTION_NAME} is ready."
