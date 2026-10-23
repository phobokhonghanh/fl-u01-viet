import os
from dataclasses import dataclass
from typing import Optional, Any
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        pass

@dataclass
class Settings:
    pass_admin: Optional[str] = None
    keys_filename: str = "keys.json"
    retry_max_attempts: int = 10
    retry_initial_delay: float = 15.0
    retry_backoff_factor: float = 1.5

    # Cloudflare KV & D1 Settings
    database_url: Optional[str] = None

    # Payment - SePay Payment Gateway
    sepay_merchant_id: Optional[str] = None
    sepay_secret_key: Optional[str] = None
    sepay_env: str = "sandbox"              # "sandbox" | "production"
    sepay_ipn_secret_key: Optional[str] = None

    # URL frontend để build success/error/cancel callback URL
    frontend_base_url: str = "http://localhost:3000"

    # Telegram Notification Settings
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_thread_id: Optional[int] = None

    # Pricing Settings
    price_lite: str = "400.000 VNĐ / tháng"
    price_plus: str = "700.000 VNĐ / tháng"

    # Version Settings
    min_client_version: str = "2.0"

    @classmethod
    def from_env(cls, env_obj: Optional[Any] = None, env_path: Optional[str] = None) -> "Settings":
        load_dotenv(dotenv_path=env_path)

        def get_val(key: str, default: Optional[str] = None) -> Optional[str]:
            val = None
            if env_obj is not None:
                if isinstance(env_obj, dict):
                    val = env_obj.get(key)
                else:
                    val = getattr(env_obj, key, None)
                    if val is None and hasattr(env_obj, "get"):
                        try:
                            val = env_obj.get(key)
                        except Exception:
                            pass
            if val is None:
                val = os.getenv(key, default)
            return str(val).strip() if val is not None else default

        thread_id_val = get_val("TELEGRAM_THREAD_ID")
        telegram_thread_id = None
        if thread_id_val:
            try:
                telegram_thread_id = int(thread_id_val)
            except ValueError:
                pass

        return cls(
            pass_admin=get_val("PASS_ADMIN", "admin"),
            keys_filename=get_val("KEYS_FILENAME", "keys.json"),
            retry_max_attempts=int(get_val("RETRY_MAX_ATTEMPTS", "10")),
            retry_initial_delay=float(get_val("RETRY_INITIAL_DELAY", "15.0")),
            retry_backoff_factor=float(get_val("RETRY_BACKOFF_FACTOR", "1.5")),
            database_url=get_val("DATABASE_URL"),
            sepay_merchant_id=get_val("SEPAY_MERCHANT_ID"),
            sepay_secret_key=get_val("SEPAY_SECRET_KEY"),
            sepay_env=get_val("SEPAY_ENV", "sandbox"),
            sepay_ipn_secret_key=get_val("SEPAY_IPN_SECRET_KEY"),
            frontend_base_url=get_val("FRONTEND_BASE_URL", "http://localhost:3000"),
            telegram_bot_token=get_val("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=get_val("TELEGRAM_CHAT_ID"),
            telegram_thread_id=telegram_thread_id,
            price_lite=get_val("PRICE_LITE") or cls.price_lite,
            price_plus=get_val("PRICE_PLUS") or cls.price_plus,
            min_client_version=get_val("MIN_CLIENT_VERSION") or cls.min_client_version,
        )
