#!/usr/bin/env python3
"""
Migration Script - Nâng cấp dữ liệu License Keys để hỗ trợ trường 'level'.
Chạy script tại thư mục backend/.
"""

import os
import sys
import argparse
import json
import logging
from datetime import datetime

# Điều chỉnh sys.path để import được các modules từ backend
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import Settings
from core.s3_storage import S3Storage
from models.schemas import KeyRecord, normalize_key_level, DEFAULT_KEY_LEVEL

# Cấu hình logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("migrate_keys")


def migrate_records(records_data: list, default_level: str) -> tuple[list, int]:
    """Bổ sung trường level cho các bản ghi key nếu chưa có."""
    migrated_count = 0
    updated_records = []

    for item in records_data:
        if not isinstance(item, dict):
            updated_records.append(item)
            continue

        # Nếu chưa có trường 'level', tiến hành bổ sung
        if "level" not in item:
            item["level"] = default_level
            migrated_count += 1
            logger.info(f"Bổ sung level '{default_level}' cho key: {item.get('key')} (Name: {item.get('name')})")
        else:
            # Chuẩn hóa level hiện tại nếu có
            item["level"] = normalize_key_level(item["level"])

        updated_records.append(item)

    return updated_records, migrated_count


def migrate_local(file_path: str, default_level: str):
    """Di trú dữ liệu từ file local."""
    if not os.path.exists(file_path):
        logger.error(f"Không tìm thấy file local tại đường dẫn: {file_path}")
        sys.exit(1)

    logger.info(f"Bắt đầu đọc file key local: {file_path}")
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"Lỗi khi đọc/parse file JSON local: {e}")
        sys.exit(1)

    if not isinstance(data, list):
        logger.error("Định dạng file JSON không hợp lệ. Phải là một danh sách các key records.")
        sys.exit(1)

    # Backup file local trước khi ghi đè
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{file_path}.backup_{timestamp}"
    try:
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"Đã tạo file backup local thành công: {backup_path}")
    except Exception as e:
        logger.error(f"Lỗi khi tạo file backup local: {e}")
        sys.exit(1)

    # Thực hiện di trú
    updated_data, migrated_count = migrate_records(data, default_level)

    # Ghi đè lại file local
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(updated_data, f, ensure_ascii=False, indent=2)
        logger.info(f"Hoàn tất! Đã di trú {migrated_count} bản ghi và ghi đè file local: {file_path}")
    except Exception as e:
        logger.error(f"Lỗi khi ghi lại file local: {e}")
        sys.exit(1)


def migrate_s3(default_level: str):
    """Di trú dữ liệu trên S3."""
    settings = Settings.from_env()
    s3_key = settings.keys_filename
    logger.info(f"Khởi tạo kết nối S3 để di trú file: {s3_key}")

    if not settings.s3_bucket:
        logger.error("Không tìm thấy cấu hình S3_BUCKET_NAME trong env.")
        sys.exit(1)

    s3_storage = S3Storage(settings)
    content = s3_storage.get_object(s3_key)

    if content is None:
        logger.error(f"Không thể tải file keys từ S3 ({s3_key}). Vui lòng kiểm tra lại cấu hình S3.")
        sys.exit(1)

    try:
        data = json.loads(content)
    except Exception as e:
        logger.error(f"Lỗi parse JSON dữ liệu tải từ S3: {e}")
        sys.exit(1)

    if not isinstance(data, list):
        logger.error("Dữ liệu keys tải từ S3 không phải là một danh sách.")
        sys.exit(1)

    # Tạo backup trên S3 trước khi lưu đè
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_s3_key = f"{s3_key}.backup_{timestamp}"
    logger.info(f"Tạo file backup trên S3: {backup_s3_key}")
    backup_success = s3_storage.put_object(backup_s3_key, content)
    if not backup_success:
        logger.error("Không thể ghi file backup lên S3. Hủy bỏ quá trình di trú để đảm bảo an toàn.")
        sys.exit(1)
    logger.info("Ghi file backup lên S3 thành công.")

    # Thực hiện di trú
    updated_data, migrated_count = migrate_records(data, default_level)

    # Ghi đè lên S3
    new_content = json.dumps(updated_data, ensure_ascii=False, indent=2)
    save_success = s3_storage.put_object(s3_key, new_content)
    if not save_success:
        logger.error("Ghi đè file keys lên S3 thất bại!")
        sys.exit(1)

    logger.info(f"Hoàn tất! Đã di trú {migrated_count} bản ghi và cập nhật lên S3 key: {s3_key}")


def main():
    parser = argparse.ArgumentParser(description="Migration script nâng cấp License Keys level.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--local-file", help="Đường dẫn tới file keys.json cục bộ cần di trú")
    group.add_argument("--s3", action="store_true", help="Di trú trực tiếp file keys trên S3 sử dụng cấu hình env")
    parser.add_argument("--level", default=DEFAULT_KEY_LEVEL, choices=["lite", "plus"],
                        help=f"Level mặc định để gán cho các key cũ chưa phân hạng (mặc định: {DEFAULT_KEY_LEVEL})")

    args = parser.parse_args()

    default_level = normalize_key_level(args.level)
    logger.info(f"Level mặc định được chọn cho key cũ: '{default_level}'")

    if args.local_file:
        migrate_local(args.local_file, default_level)
    elif args.s3:
        migrate_s3(default_level)


if __name__ == "__main__":
    main()
