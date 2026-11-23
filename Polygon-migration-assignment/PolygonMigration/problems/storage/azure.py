import logging

from azure.core.exceptions import AzureError
from azure.identity import UsernamePasswordCredential
from azure.storage.blob import BlobServiceClient

from .base import StorageBackend, StorageError

logger = logging.getLogger(__name__)


class AzureBlobStorageBackend(StorageBackend):
    """
    Stores files in an Azure Blob Storage container.

    Authenticates with an AAD username and password, as the original
    AzureBlobManager did. This flow does not work for users with MFA enabled.
    """

    name = "Azure Blob Storage"

    def __init__(self, account_url, tenant_id, client_id, username, password, container_name):
        credential = UsernamePasswordCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            username=username,
            password=password,
        )
        service = BlobServiceClient(account_url=account_url, credential=credential)
        self.container = service.get_container_client(container_name)

    def upload(self, key, data):
        try:
            self.container.upload_blob(name=key, data=data, overwrite=True)
        except AzureError as e:
            raise StorageError(f"Azure upload failed for '{key}': {e}") from e
        logger.info("Uploaded %s (%d bytes) to Azure", key, len(data))

    def list_keys(self, prefix):
        try:
            return [blob.name for blob in self.container.list_blobs(name_starts_with=prefix)]
        except AzureError as e:
            raise StorageError(f"Azure list failed for prefix '{prefix}': {e}") from e

    def delete_prefix(self, prefix):
        keys = self.list_keys(prefix)
        try:
            for key in keys:
                self.container.delete_blob(key)
        except AzureError as e:
            raise StorageError(f"Azure delete failed for prefix '{prefix}': {e}") from e
        logger.info("Deleted %d blobs under %s in Azure", len(keys), prefix)
        return len(keys)
