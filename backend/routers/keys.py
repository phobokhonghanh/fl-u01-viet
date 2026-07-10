"""
Keys Router - API endpoints quản lý License Key.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Response, BackgroundTasks
from pydantic import BaseModel

from config.settings import Settings
from core import key_manager
from core.telegram import send_telegram_notification
from models.schemas import DEFAULT_KEY_PRODUCT, is_valid_key_product, normalize_key_product

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["keys"])


class KeyRequest(BaseModel):
    key: str
    machine_id: Optional[str] = None
    product: Optional[str] = None
    client_version: Optional[str] = None


class AdminKeyListRequest(BaseModel):
    password: str


class AdminKeyAddRequest(BaseModel):
    name: str
    password: str
    days: Optional[int] = 30
    forever: Optional[bool] = False
    product: Optional[str] = DEFAULT_KEY_PRODUCT
    level: Optional[str] = "lite"


class AdminKeyDeleteRequest(BaseModel):
    password: str
    key: str


class AdminKeyResetRequest(BaseModel):
    password: str
    key: str


class AdminKeyExportRequest(BaseModel):
    password: str


def _check_admin(password: str, settings: Settings):
    if password != settings.pass_admin:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid admin password")


def parse_version(v_str: Optional[str]) -> tuple[int, ...]:
    if not v_str:
        return (0, 0)
    try:
        parts = tuple(int(x) for x in v_str.strip().split(".") if x.isdigit())
        return parts if parts else (0, 0)
    except Exception:
        return (0, 0)


@router.post("/key/active")
async def verify_key(req: KeyRequest):
    """Verify if a key is active and matches machine_id (locking)."""
    settings = Settings.from_env()
    product = normalize_key_product(req.product)
    if not is_valid_key_product(product):
        raise HTTPException(status_code=400, detail="Invalid product")
        
    if product == "fotello":
        min_ver = parse_version(settings.min_client_version)
        client_ver = parse_version(req.client_version)
        if client_ver < min_ver:
            return {
                "status": "error",
                "valid": False,
                "message": f"Phiên bản Fotello của bạn đã cũ. Vui lòng tải phiên bản mới nhất {settings.min_client_version} để tiếp tục sử dụng."
            }

    record = key_manager.verify_and_get_key(settings.keys_filename, req.key, req.machine_id, product)
    if not record:
        raise HTTPException(status_code=403, detail="Key is invalid, expired, used on another machine, or not allowed for this product")
    return {
        "status": "ok",
        "valid": True,
        "level": record.level,
        "pricing": {
            "lite": settings.price_lite,
            "plus": settings.price_plus
        }
    }


@router.post("/admin/keys/list")
async def admin_list_keys(req: AdminKeyListRequest):
    """List all keys (Admin only)."""
    settings = Settings.from_env()
    _check_admin(req.password, settings)
    keys = key_manager.load_keys(settings.keys_filename)
    return [k.to_dict() for k in keys]


@router.post("/admin/keys/add")
async def admin_add_key(req: AdminKeyAddRequest, background_tasks: BackgroundTasks):
    """Add or update a key (Admin only)."""
    settings = Settings.from_env()
    _check_admin(req.password, settings)

    expiry = None
    if not req.forever and req.days:
        expiry_dt = datetime.utcnow() + timedelta(days=req.days)
        expiry = expiry_dt.isoformat() + "Z"

    product = normalize_key_product(req.product)
    if not is_valid_key_product(product):
        raise HTTPException(status_code=400, detail="Invalid product")

    record, status = key_manager.add_or_update_key_by_name(settings.keys_filename, req.name, expiry, product, req.level)

    # Notify telegram in the background
    action_str = "CREATED" if status == "new" else ("UPDATED" if status == "updated" else "EXISTS")
    status_emoji = "🔑" if status == "new" else ("🔄" if status == "updated" else "ℹ️")

    msg = (
        f"{status_emoji} <b>License Key {action_str}</b>\n"
        f"• <b>Name</b>: <code>{record.name}</code>\n"
        f"• <b>Product</b>: <code>{record.product}</code>\n"
        f"• <b>Level</b>: <code>{record.level}</code>\n"
        f"• <b>Expiry</b>: <code>{record.expires_at or 'Forever'}</code>"
    )
    background_tasks.add_task(send_telegram_notification, msg)

    return {"status": status, "record": record.to_dict()}


@router.post("/admin/keys/delete")
async def admin_delete_key(req: AdminKeyDeleteRequest, background_tasks: BackgroundTasks):
    """Delete a key (Admin only)."""
    settings = Settings.from_env()
    _check_admin(req.password, settings)

    # Load keys to find the one we are about to delete for the telegram notification
    keys = key_manager.load_keys(settings.keys_filename)
    deleted_record = None
    for k in keys:
        if k.key == req.key or k.name == req.key:
            deleted_record = k
            break

    success = key_manager.delete_key(settings.keys_filename, req.key)
    if success:
        if deleted_record:
            msg = (
                f"🗑️ <b>License Key DELETED</b>\n"
                f"• <b>Name</b>: <code>{deleted_record.name}</code>\n"
                f"• <b>Product</b>: <code>{deleted_record.product}</code>\n"
                f"• <b>Expiry</b>: <code>{deleted_record.expires_at or 'Forever'}</code>"
            )
        else:
            msg = (
                f"🗑️ <b>License Key DELETED</b>\n"
                f"• <b>Identifier</b>: <code>{req.key}</code>"
            )
        background_tasks.add_task(send_telegram_notification, msg)
        return {"status": "ok", "message": "Key deleted successfully"}
    raise HTTPException(status_code=404, detail="Key not found")


@router.post("/admin/keys/reset")
async def admin_reset_key(req: AdminKeyResetRequest, background_tasks: BackgroundTasks):
    """Reset the machine_id of a key (Admin only)."""
    settings = Settings.from_env()
    _check_admin(req.password, settings)

    record = key_manager.reset_key_machine(settings.keys_filename, req.key)
    if not record:
        raise HTTPException(status_code=404, detail="Key not found")

    # Notify telegram in the background
    msg = (
        f"🔄 <b>License Key RESET</b>\n"
        f"• <b>Name</b>: <code>{record.name}</code>\n"
        f"• <b>Product</b>: <code>{record.product}</code>\n"
        f"• <b>Status</b>: <code>Machine ID Cleared</code>"
    )
    background_tasks.add_task(send_telegram_notification, msg)

    return {"status": "ok", "record": record.to_dict()}


@router.post("/admin/keys/import")
async def admin_import_keys(password: str = Form(...), file: UploadFile = File(...)):
    """Import keys from JSON file (Admin only)."""
    settings = Settings.from_env()
    _check_admin(password, settings)

    try:
        content = await file.read()
        data = json.loads(content.decode("utf-8"))
        if not isinstance(data, list):
            raise HTTPException(status_code=400, detail="Invalid JSON format: Expected a list of key records")
        imported = key_manager.import_keys(settings.keys_filename, data)
        return {"status": "ok", "message": f"Successfully imported {imported} new keys"}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON format")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")


@router.post("/admin/keys/export")
async def admin_export_keys(req: AdminKeyExportRequest):
    """Export keys as JSON file (Admin only)."""
    settings = Settings.from_env()
    _check_admin(req.password, settings)

    keys = key_manager.load_keys(settings.keys_filename)
    if not keys:
        raise HTTPException(status_code=404, detail="Keys data not found on S3")
    content = json.dumps([key.to_dict() for key in keys], ensure_ascii=False, indent=2)

    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename=keys_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        }
    )
