#!/usr/bin/env python3
"""
Reset All Keys Script - Đặt trường machine_id của tất cả các Key về None.
Chạy script tại thư mục backend/.
"""

import os
import sys
import logging

# Điều chỉnh sys.path để import các module từ backend
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import Settings
from core.key_manager import reset_all_keys_machine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("reset_all_keys")


def main():
    settings = Settings.from_env()
    s3_key = settings.keys_filename
    logger.info(f"Bắt đầu reset tất cả machine_id trong file: {s3_key}")
    
    count = reset_all_keys_machine(s3_key)
    logger.info(f"Đã reset thành công machine_id cho {count} keys.")


if __name__ == "__main__":
    main()
