from src.config import S3_BUCKET_DOCS, get_s3_client


def create_buckets() -> None:
    s3 = get_s3_client()
    existing = {bucket["Name"] for bucket in s3.list_buckets().get("Buckets", [])}
    if S3_BUCKET_DOCS not in existing:
        s3.create_bucket(Bucket=S3_BUCKET_DOCS)
        print(f"Bucket '{S3_BUCKET_DOCS}' created.")


if __name__ == "__main__":
    create_buckets()
