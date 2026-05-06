import os
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

@dataclass
class Settings:
    pass_admin: Optional[str] = None
    keys_filename: str = "keys.json"
    retry_max_attempts: int = 10
    retry_initial_delay: float = 15.0
    retry_backoff_factor: float = 1.5

    # S3 Settings
    s3_region: Optional[str] = None
    s3_endpoint: Optional[str] = None
    s3_access_key: Optional[str] = None
    s3_secret_key: Optional[str] = None
    s3_bucket: Optional[str] = None

    # Payment - Database
    database_url: Optional[str] = None

    # Payment - SePay Payment Gateway
    sepay_merchant_id: Optional[str] = None
    sepay_secret_key: Optional[str] = None
    sepay_env: str = "sandbox"              # "sandbox" | "production"

    # Payment - IPN (Instant Payment Notification)
    # Secret key riêng để xác thực callback từ SePay → backend (Header X-Secret-Key)
    # Lấy từ SePay Dashboard → Cổng thanh toán → Cấu hình → IPN
    sepay_ipn_secret_key: Optional[str] = None

    # URL frontend để build success/error/cancel callback URL
    frontend_base_url: str = "http://localhost:3000"

    @classmethod
    def from_env(cls, env_path: Optional[str] = None) -> "Settings":
        load_dotenv(dotenv_path=env_path)

        return cls(
            pass_admin=os.getenv("PASS_ADMIN", "admin"),
            keys_filename=os.getenv("KEYS_FILENAME", "keys.json"),
            retry_max_attempts=int(os.getenv("RETRY_MAX_ATTEMPTS", "10")),
            retry_initial_delay=float(os.getenv("RETRY_INITIAL_DELAY", "15.0")),
            retry_backoff_factor=float(os.getenv("RETRY_BACKOFF_FACTOR", "1.5")),
            s3_region=os.getenv("S3_REGION"),
            s3_endpoint=os.getenv("S3_ENDPOINT"),
            s3_access_key=os.getenv("S3_ACCESS_KEY"),
            s3_secret_key=os.getenv("S3_SECRET_KEY"),
            s3_bucket=os.getenv("S3_BUCKET_NAME"),
            database_url=os.getenv("DATABASE_URL"),
            sepay_merchant_id=os.getenv("SEPAY_MERCHANT_ID"),
            sepay_secret_key=os.getenv("SEPAY_SECRET_KEY"),
            sepay_env=os.getenv("SEPAY_ENV", "sandbox"),
            sepay_ipn_secret_key=os.getenv("SEPAY_IPN_SECRET_KEY"),
            frontend_base_url=os.getenv("FRONTEND_BASE_URL", "http://localhost:3000"),
        )
