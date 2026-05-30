"""
Key Manager - Utilities for tracking and validating executable keys.
"""

import json
import logging
from typing import List, Optional
from models.schemas import DEFAULT_KEY_PRODUCT, KeyRecord, is_valid_key_product, normalize_key_product
from config.settings import Settings
from core.s3_storage import S3Storage

logger = logging.getLogger(__name__)

# Initialize S3 Storage
settings = Settings.from_env()
s3_storage = S3Storage(settings)

def load_keys(s3_key: str) -> List[KeyRecord]:
    """Load the key records from S3."""
    try:
        content = s3_storage.get_object(s3_key)
        
        if content is None:
            logger.warning(f"Không thể lấy S3 key: {s3_key}. Có thể key không tồn tại hoặc đã xảy ra lỗi.")
            return []
        
        logger.info(f"Tải thành công nội dung cho S3 key: {s3_key} (độ dài: {len(content)})")
        data = json.loads(content)
        records = [KeyRecord.from_dict(item) for item in data]
        logger.info(f"Đã phân tích {len(records)} bản ghi từ S3 key: {s3_key}")
        return records
    except Exception as e:
        logger.error(f"Lỗi nghiêm trọng khi tải keys từ S3 ({s3_key}): {e}", exc_info=True)
        raise

def save_keys(s3_key: str, records: List[KeyRecord]) -> None:
    """Save key records to S3."""
    try:
        content = json.dumps([record.to_dict() for record in records], ensure_ascii=False, indent=2)
        success = s3_storage.put_object(s3_key, content)
        if not success:
            raise IOError(f"Không thể lưu keys lên S3 sau nhiều lần thử.")
    except Exception as e:
        logger.error(f"Lưu keys lên S3 thất bại ({s3_key}): {e}")
        raise

import random
import string

def random_key_string(length: int = 10) -> str:
    """Generate a random alphanumeric string of a given length."""
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

def check_key(
    s3_key: str,
    key_str: str,
    machine_id: Optional[str] = None,
    product: Optional[str] = None,
) -> bool:
    """
    Check if the given key is valid, not expired, matches product, and matches machine_id.
    """
    requested_product = normalize_key_product(product)
    if not is_valid_key_product(requested_product):
        logger.warning(f"Product không hợp lệ khi active key: {requested_product}")
        return False

    records = load_keys(s3_key)
    for record in records:
        if record.key == key_str:
            if record.is_expired():
                logger.warning(f"Key đã hết hạn hoặc không hoạt động: {key_str}")
                return False

            if normalize_key_product(record.product) != requested_product:
                logger.warning(f"Key {key_str} không hợp lệ cho product: {requested_product}")
                return False
            
            if record.machine_id:
                if machine_id and record.machine_id != machine_id:
                    logger.warning(f"Key {key_str} đã được sử dụng trên máy khác: {record.machine_id} != {machine_id}")
                    return False
            elif machine_id:
                record.machine_id = machine_id
                save_keys(s3_key, records)
                logger.info(f"Key {key_str} đã được gắn với máy: {machine_id}")
                
            return True
                
    logger.warning(f"Không tìm thấy key: {key_str}")
    return False

def add_or_update_key_by_name(
    s3_key: str,
    name: str,
    expires_at: Optional[str] = None,
    product: Optional[str] = None,
):
    """
    Add or update a key by name.
    """
    normalized_product = normalize_key_product(product)
    records = load_keys(s3_key)
    for record in records:
        if record.name == name:
            record.product = normalized_product
            if not record.is_expired():
                save_keys(s3_key, records)
                return record, "valid"
            else:
                record.expires_at = expires_at
                record.is_active = True
                save_keys(s3_key, records)
                return record, "updated"
            
    new_key = random_key_string(10)
    new_record = KeyRecord(key=new_key, name=name, is_active=True, expires_at=expires_at, product=normalized_product)
    records.append(new_record)
    save_keys(s3_key, records)
    return new_record, "new"

def delete_key(s3_key: str, key_to_delete: str) -> bool:
    """Delete a key by its name or key value."""
    records = load_keys(s3_key)
    original_count = len(records)
    records = [r for r in records if r.key != key_to_delete and r.name != key_to_delete]
    
    if len(records) < original_count:
        save_keys(s3_key, records)
        return True
    return False

def import_keys(s3_key: str, new_keys_data: list) -> int:
    """Import valid keys from JSON array."""
    records = load_keys(s3_key)
    existing_names = {r.name for r in records if r.name}
    existing_keys = {r.key for r in records if r.key}
    
    imported_count = 0
    for item in new_keys_data:
        try:
            if not isinstance(item, dict):
                logger.warning(f"Nhập key thất bại vì mục không phải object: {item}")
                continue
            if not str(item.get("key") or "").strip():
                logger.warning(f"Nhập key thất bại vì thiếu key: {item}")
                continue
            product = normalize_key_product(item.get("product", DEFAULT_KEY_PRODUCT))
            if not is_valid_key_product(product):
                logger.warning(f"Nhập key thất bại vì product không hợp lệ: {product}")
                continue
            item = {**item, "product": product}
            new_record = KeyRecord.from_dict(item)
            if new_record.key not in existing_keys and (not new_record.name or new_record.name not in existing_names):
                records.append(new_record)
                existing_keys.add(new_record.key)
                existing_names.add(new_record.name)
                imported_count += 1
        except Exception as e:
            logger.warning(f"Nhập key thất bại cho mục {item}: {e}")
            continue
            
    if imported_count > 0:
        save_keys(s3_key, records)
        
    return imported_count
