import json
import logging
from config.settings import Settings

logger = logging.getLogger(__name__)


async def send_telegram_notification(text: str, env=None) -> bool:
    """
    Sends a message to the configured Telegram chat/channel.
    Catches all exceptions internally to prevent telegram errors from failing the caller.
    """
    settings = Settings.from_env(env)
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id

    if not token or not chat_id:
        logger.debug("Telegram bot token or chat ID not configured. Skipping notification.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if settings.telegram_thread_id is not None:
        payload["message_thread_id"] = settings.telegram_thread_id

    try:
        from js import Object
        opts = Object()
        opts.method = "POST"
        opts.headers = Object()
        opts.headers["Content-Type"] = "application/json"
        opts.body = json.dumps(payload)
        from workers import fetch
        await fetch(url, opts)
        logger.info("Telegram notification sent successfully.")
        return True
    except Exception as e:
        logger.error(f"Failed to send Telegram notification: {e}")
        return False

