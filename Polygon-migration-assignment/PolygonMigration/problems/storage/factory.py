from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

# Settings each provider needs; the names match the environment variables in .env.example.
# S3 keys are optional: without them boto3 uses its default credential chain (IAM role, AWS_PROFILE).
REQUIRED_SETTINGS = {
    "s3": ["S3_BUCKET_NAME"],
    "azure": [
        "AZURE_STORAGE_ACCOUNT_URL",
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_ID",
        "AZURE_USERNAME",
        "AZURE_PASSWORD",
        "AZURE_CONTAINER_NAME",
    ],
}


def _require(provider):
    missing = [name for name in REQUIRED_SETTINGS[provider] if not getattr(settings, name, None)]
    if missing:
        raise ImproperlyConfigured(
            f"STORAGE_PROVIDER={provider} needs these environment variables: {', '.join(missing)}"
        )


def get_storage():
    """Builds the storage backend selected by the STORAGE_PROVIDER setting."""
    provider = (getattr(settings, "STORAGE_PROVIDER", "") or "").strip().lower()

    if provider == "s3":
        _require("s3")
        from .s3 import S3StorageBackend

        return S3StorageBackend(
            bucket=settings.S3_BUCKET_NAME,
            region=settings.S3_REGION,
            access_key_id=settings.S3_ACCESS_KEY_ID,
            secret_access_key=settings.S3_SECRET_ACCESS_KEY,
            endpoint_url=settings.S3_ENDPOINT_URL,
        )

    if provider == "azure":
        _require("azure")
        from .azure import AzureBlobStorageBackend

        return AzureBlobStorageBackend(
            account_url=settings.AZURE_STORAGE_ACCOUNT_URL,
            tenant_id=settings.AZURE_TENANT_ID,
            client_id=settings.AZURE_CLIENT_ID,
            username=settings.AZURE_USERNAME,
            password=settings.AZURE_PASSWORD,
            container_name=settings.AZURE_CONTAINER_NAME,
        )

    raise ImproperlyConfigured(
        f"Unknown STORAGE_PROVIDER '{provider}'. Supported values: {', '.join(REQUIRED_SETTINGS)}"
    )
