"""
Key Manager - Utilities for tracking and validating executable keys.
Supports Cloudflare KV with local fallback.
"""

import json
import logging
import random
import string
from typing import Any, List, Optional
from models.schemas import (
    DEFAULT_KEY_PRODUCT,
    KeyRecord,
    is_valid_key_product,
    normalize_key_product,
    normalize_key_level,
    is_valid_key_level,
    DEFAULT_KEY_LEVEL
)
from config.settings import Settings
from core.kv_storage import KVStorage

logger = logging.getLogger(__name__)


def _get_active_env(env: Any = None) -> Any:
    if env is not None:
        return env
    try:
        from core.env_context import get_current_env
        return get_current_env()
    except Exception:
        return None


def load_keys(s3_key: str, env: Any = None) -> List[KeyRecord]:
    """Load the key records from KV or local file."""
    active_env = _get_active_env(env)
    storage = KVStorage(active_env)
    
    # Check if we can read synchronously from local file when no KV binding
    if active_env is None or not hasattr(active_env, "KEYS_KV") or active_env.KEYS_KV is None:
        import os
        if not os.path.exists(s3_key):
            logger.warning(f"File key không tồn tại: {s3_key}")
            return []
        try:
            with open(s3_key, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.error(f"Lỗi khi đọc file local {s3_key}: {e}")
            return []
    else:
        # Running in Cloudflare Worker with KV binding
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # In async context, create task or wait
                content = storage.env.KEYS_KV.get(s3_key, type="text")
                if hasattr(content, "__await__") or asyncio.iscoroutine(content):
                    import concurrent.futures
                    # If we're inside a coroutine, callers should use async_load_keys
                    content = asyncio.run_coroutine_threadsafe(storage.get_text(s3_key), loop).result()
            else:
                content = loop.run_until_complete(storage.get_text(s3_key))
        except Exception:
            content = None

    if not content:
        return []

    try:
        data = json.loads(content)
        records = [KeyRecord.from_dict(item) for item in data]
        return records
    except Exception as e:
        logger.error(f"Lỗi phân tích bản ghi từ key ({s3_key}): {e}")
        return []


async def async_load_keys(s3_key: str, env: Any = None) -> List[KeyRecord]:
    """Async load key records from KV or local file."""
    active_env = _get_active_env(env)
    storage = KVStorage(active_env)
    content = await storage.get_text(s3_key)
    if not content:
        return []
    try:
        data = json.loads(content)
        return [KeyRecord.from_dict(item) for item in data]
    except Exception as e:
        logger.error(f"Lỗi parse keys JSON ({s3_key}): {e}")
        return []


def save_keys(s3_key: str, records: List[KeyRecord], env: Any = None) -> None:
    """Save key records to KV or local file."""
    active_env = _get_active_env(env)
    storage = KVStorage(active_env)
    content = json.dumps([record.to_dict() for record in records], ensure_ascii=False, indent=2)

    if active_env is None or not hasattr(active_env, "KEYS_KV") or active_env.KEYS_KV is None:
        try:
            with open(s3_key, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            logger.error(f"Lỗi khi ghi file local {s3_key}: {e}")
            raise
    else:
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(storage.put_text(s3_key, content), loop).result()
            else:
                loop.run_until_complete(storage.put_text(s3_key, content))
        except Exception as e:
            logger.error(f"Lưu keys lên KV thất bại ({s3_key}): {e}")
            raise


async def async_save_keys(s3_key: str, records: List[KeyRecord], env: Any = None) -> None:
    """Async save key records to KV or local file."""
    active_env = _get_active_env(env)
    storage = KVStorage(active_env)
    content = json.dumps([record.to_dict() for record in records], ensure_ascii=False, indent=2)
    success = await storage.put_text(s3_key, content)
    if not success:
        raise IOError(f"Không thể lưu keys lên KV ({s3_key}).")


def random_key_string(length: int = 10) -> str:
    """Generate a random alphanumeric string of a given length."""
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for _ in range(length))


async def async_verify_and_get_key(
    s3_key: str,
    key_str: str,
    machine_id: Optional[str] = None,
    product: Optional[str] = None,
    env: Any = None,
) -> Optional[KeyRecord]:
    requested_product = normalize_key_product(product)
    if not is_valid_key_product(requested_product):
        logger.warning(f"Product không hợp lệ khi active key: {requested_product}")
        return None

    records = await async_load_keys(s3_key, env)
    for record in records:
        if record.key == key_str:
            if record.is_expired():
                logger.warning(f"Key đã hết hạn hoặc không hoạt động: {key_str}")
                return None

            if normalize_key_product(record.product) != requested_product:
                logger.warning(f"Key {key_str} không hợp lệ cho product: {requested_product}")
                return None

            if record.machine_id:
                if machine_id and record.machine_id != machine_id:
                    logger.warning(f"Key {key_str} đã được sử dụng trên máy khác: {record.machine_id} != {machine_id}")
                    return None
            elif machine_id:
                record.machine_id = machine_id
                await async_save_keys(s3_key, records, env)
                logger.info(f"Key {key_str} đã được gắn với máy: {machine_id}")

            return record

    logger.warning(f"Không tìm thấy key: {key_str}")
    return None


def verify_and_get_key(
    s3_key: str,
    key_str: str,
    machine_id: Optional[str] = None,
    product: Optional[str] = None,
    env: Any = None,
) -> Optional[KeyRecord]:
    requested_product = normalize_key_product(product)
    if not is_valid_key_product(requested_product):
        return None

    records = load_keys(s3_key, env)
    for record in records:
        if record.key == key_str:
            if record.is_expired():
                return None

            if normalize_key_product(record.product) != requested_product:
                return None

            if record.machine_id:
                if machine_id and record.machine_id != machine_id:
                    return None
            elif machine_id:
                record.machine_id = machine_id
                save_keys(s3_key, records, env)

            return record

    return None


def check_key(
    s3_key: str,
    key_str: str,
    machine_id: Optional[str] = None,
    product: Optional[str] = None,
    env: Any = None,
) -> bool:
    return verify_and_get_key(s3_key, key_str, machine_id, product, env) is not None


async def async_add_or_update_key_by_name(
    s3_key: str,
    name: str,
    expires_at: Optional[str] = None,
    product: Optional[str] = None,
    level: Optional[str] = None,
    env: Any = None,
):
    normalized_product = normalize_key_product(product)
    normalized_level = normalize_key_level(level)
    records = await async_load_keys(s3_key, env)
    for record in records:
        if record.name == name:
            changes: dict = {}
            if record.product != normalized_product:
                changes["product"] = (record.product, normalized_product)
            if record.level != normalized_level:
                changes["level"] = (record.level, normalized_level)

            record.product = normalized_product
            record.level = normalized_level

            if not record.is_expired():
                if changes:
                    await async_save_keys(s3_key, records, env)
                    return record, "updated", changes
                return record, "valid", {}
            else:
                old_expires = record.expires_at
                record.expires_at = expires_at
                record.is_active = True
                if expires_at != old_expires:
                    changes["expires_at"] = (old_expires, expires_at)
                await async_save_keys(s3_key, records, env)
                return record, "updated", changes

    new_key = random_key_string(10)
    new_record = KeyRecord(
        key=new_key,
        name=name,
        is_active=True,
        expires_at=expires_at,
        product=normalized_product,
        level=normalized_level
    )
    records.append(new_record)
    await async_save_keys(s3_key, records, env)
    return new_record, "new", {}


def add_or_update_key_by_name(
    s3_key: str,
    name: str,
    expires_at: Optional[str] = None,
    product: Optional[str] = None,
    level: Optional[str] = None,
    env: Any = None,
):
    normalized_product = normalize_key_product(product)
    normalized_level = normalize_key_level(level)
    records = load_keys(s3_key, env)
    for record in records:
        if record.name == name:
            changes: dict = {}
            if record.product != normalized_product:
                changes["product"] = (record.product, normalized_product)
            if record.level != normalized_level:
                changes["level"] = (record.level, normalized_level)

            record.product = normalized_product
            record.level = normalized_level

            if not record.is_expired():
                if changes:
                    save_keys(s3_key, records, env)
                    return record, "updated", changes
                return record, "valid", {}
            else:
                old_expires = record.expires_at
                record.expires_at = expires_at
                record.is_active = True
                if expires_at != old_expires:
                    changes["expires_at"] = (old_expires, expires_at)
                save_keys(s3_key, records, env)
                return record, "updated", changes

    new_key = random_key_string(10)
    new_record = KeyRecord(
        key=new_key,
        name=name,
        is_active=True,
        expires_at=expires_at,
        product=normalized_product,
        level=normalized_level
    )
    records.append(new_record)
    save_keys(s3_key, records, env)
    return new_record, "new", {}


async def async_delete_key(s3_key: str, key_to_delete: str, env: Any = None) -> bool:
    records = await async_load_keys(s3_key, env)
    original_count = len(records)
    records = [r for r in records if r.key != key_to_delete and r.name != key_to_delete]
    if len(records) < original_count:
        await async_save_keys(s3_key, records, env)
        return True
    return False


def delete_key(s3_key: str, key_to_delete: str, env: Any = None) -> bool:
    records = load_keys(s3_key, env)
    original_count = len(records)
    records = [r for r in records if r.key != key_to_delete and r.name != key_to_delete]
    if len(records) < original_count:
        save_keys(s3_key, records, env)
        return True
    return False


async def async_reset_key_machine(s3_key: str, key_or_name: str, env: Any = None) -> Optional[KeyRecord]:
    records = await async_load_keys(s3_key, env)
    for record in records:
        if record.key == key_or_name or record.name == key_or_name:
            record.machine_id = None
            await async_save_keys(s3_key, records, env)
            return record
    return None


def reset_key_machine(s3_key: str, key_or_name: str, env: Any = None) -> Optional[KeyRecord]:
    records = load_keys(s3_key, env)
    for record in records:
        if record.key == key_or_name or record.name == key_or_name:
            record.machine_id = None
            save_keys(s3_key, records, env)
            return record
    return None


async def async_reset_all_keys_machine(s3_key: str, env: Any = None) -> int:
    records = await async_load_keys(s3_key, env)
    reset_count = 0
    for record in records:
        if record.machine_id is not None:
            record.machine_id = None
            reset_count += 1
    if reset_count > 0:
        await async_save_keys(s3_key, records, env)
    return reset_count


def reset_all_keys_machine(s3_key: str, env: Any = None) -> int:
    records = load_keys(s3_key, env)
    reset_count = 0
    for record in records:
        if record.machine_id is not None:
            record.machine_id = None
            reset_count += 1
    if reset_count > 0:
        save_keys(s3_key, records, env)
    return reset_count


async def async_import_keys(s3_key: str, new_keys_data: list, env: Any = None) -> int:
    records = await async_load_keys(s3_key, env)
    existing_names = {r.name for r in records if r.name}
    existing_keys = {r.key for r in records if r.key}

    imported_count = 0
    for item in new_keys_data:
        try:
            if not isinstance(item, dict):
                continue
            if not str(item.get("key") or "").strip():
                continue
            product = normalize_key_product(item.get("product", DEFAULT_KEY_PRODUCT))
            if not is_valid_key_product(product):
                continue
            level = normalize_key_level(item.get("level", DEFAULT_KEY_LEVEL))
            if not is_valid_key_level(level):
                continue
            item = {**item, "product": product, "level": level}
            new_record = KeyRecord.from_dict(item)
            if new_record.key not in existing_keys and (not new_record.name or new_record.name not in existing_names):
                records.append(new_record)
                existing_keys.add(new_record.key)
                existing_names.add(new_record.name)
                imported_count += 1
        except Exception:
            continue

    if imported_count > 0:
        await async_save_keys(s3_key, records, env)

    return imported_count


def import_keys(s3_key: str, new_keys_data: list, env: Any = None) -> int:
    records = load_keys(s3_key, env)
    existing_names = {r.name for r in records if r.name}
    existing_keys = {r.key for r in records if r.key}

    imported_count = 0
    for item in new_keys_data:
        try:
            if not isinstance(item, dict):
                continue
            if not str(item.get("key") or "").strip():
                continue
            product = normalize_key_product(item.get("product", DEFAULT_KEY_PRODUCT))
            if not is_valid_key_product(product):
                continue
            level = normalize_key_level(item.get("level", DEFAULT_KEY_LEVEL))
            if not is_valid_key_level(level):
                continue
            item = {**item, "product": product, "level": level}
            new_record = KeyRecord.from_dict(item)
            if new_record.key not in existing_keys and (not new_record.name or new_record.name not in existing_names):
                records.append(new_record)
                existing_keys.add(new_record.key)
                existing_names.add(new_record.name)
                imported_count += 1
        except Exception:
            continue

    if imported_count > 0:
        save_keys(s3_key, records, env)

    return imported_count
