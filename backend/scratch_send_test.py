import asyncio
import os
import sys

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.telegram import send_telegram_notification
from config.settings import Settings

async def main():
    settings = Settings.from_env()
    print("----- Telegram Test Script -----")
    print(f"Bot Token: {settings.telegram_bot_token}")
    print(f"Chat ID: {settings.telegram_chat_id}")
    print(f"Thread ID: {settings.telegram_thread_id}")
    
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        print("\n[ERROR] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing in your .env file.")
        return

    print("\nSending message: 'dev test local'...")
    success = await send_telegram_notification("dev test local")
    if success:
        print("[SUCCESS] Message sent successfully.")
    else:
        print("[FAILED] Check the logs above or check your network/token/chat_id/thread_id.")

if __name__ == "__main__":
    asyncio.run(main())
