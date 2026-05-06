"""
S3 Storage Utility - Handles interactions with S3-compatible storage.
"""

import logging
import boto3
from botocore.config import Config
from typing import Optional, Any, List

from config.settings import Settings
from core.retry import retry_with_backoff

logger = logging.getLogger(__name__)

class S3Storage:
    """
    Utility class for S3 operations with retry logic.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.bucket = settings.s3_bucket
        
        if not all([settings.s3_access_key, settings.s3_secret_key, settings.s3_bucket]):
            self.client = None
            logger.warning("Cấu hình S3 không đầy đủ. S3Storage sẽ không hoạt động.")
            return

        # Initialize S3 client
        self.client = boto3.client(
            "s3",
            region_name=settings.s3_region,
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=Config(retries={'max_attempts': 0}) # We handle retries manually
        )

    def _ensure_client(self):
        if not self.client:
            raise ValueError("Client S3 chưa được cấu hình. Vui lòng kiểm tra các biến môi trường.")

    def list_objects(self) -> List[str]:
        """List all objects in the bucket."""
        self._ensure_client()
        
        def _list():
            response = self.client.list_objects_v2(Bucket=self.bucket)
            return [obj["Key"] for obj in response.get("Contents", [])]

        return retry_with_backoff(
            _list,
            logger,
            step=0,
            max_retries=self.settings.retry_max_attempts,
            on_retry_message="Đang thử lại liệt kê object trên S3"
        ) or []

    def put_object(self, key: str, body: str):
        """Upload a string content to S3."""
        self._ensure_client()
        
        def _put():
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body
            )
            return True

        return retry_with_backoff(
            _put,
            logger,
            step=0,
            max_retries=self.settings.retry_max_attempts,
            on_retry_message=f"Đang thử lại tải lên {key} lên S3"
        )

    def get_object(self, key: str) -> Optional[str]:
        """Download content from S3 as a string."""
        self._ensure_client()
        
        def _get():
            try:
                response = self.client.get_object(Bucket=self.bucket, Key=key)
                return response["Body"].read().decode("utf-8")
            except self.client.exceptions.NoSuchKey:
                return "NOT_FOUND" # Special marker for missing key
            except Exception as e:
                # If it's a permission/auth error, we want to know
                logger.error(f"Lỗi S3 GetObject cho {key}: {e}")
                raise

        result = retry_with_backoff(
            _get,
            logger,
            step=0,
            max_retries=self.settings.retry_max_attempts,
            on_retry_message=f"Đang thử lại tải xuống {key} từ S3"
        )
        
        if result == "NOT_FOUND":
            return None
        return result

    def delete_objects(self, keys: List[str]):
        """Delete multiple objects from S3."""
        self._ensure_client()
        if not keys:
            return

        def _delete():
            self.client.delete_objects(
                Bucket=self.bucket,
                Delete={
                    "Objects": [{"Key": k} for k in keys]
                }
            )
            return True

        return retry_with_backoff(
            _delete,
            logger,
            step=0,
            max_retries=self.settings.retry_max_attempts,
            on_retry_message="Đang thử lại xóa object trên S3"
        )
