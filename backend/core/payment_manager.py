"""
Payment Manager - Quản lý đơn hàng thanh toán qua SePay Payment Gateway.
Hỗ trợ Cloudflare D1 Database và local SQLite fallback.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import random
import string
import time
from typing import Any, List, Optional, Tuple

from config.settings import Settings
from core import d1_db
from core.env_context import get_current_env

logger = logging.getLogger(__name__)


def _get_active_env(env: Any = None) -> Any:
    return env if env is not None else get_current_env()


async def init_db(settings: Settings, env: Any = None):
    """Khởi tạo bảng orders và transactions nếu chưa tồn tại."""
    active_env = _get_active_env(env)
    await d1_db.init_db(active_env)


def _random_id(length: int = 8) -> str:
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))


def _random_token(length: int = 32) -> str:
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))


# --- Orders CRUD ---

async def create_order(settings: Settings, user_name: str, amount: float,
                       item: str = "", note: str = "", env: Any = None) -> dict:
    """Admin tạo đơn hàng mới."""
    active_env = _get_active_env(env)
    order_id = _random_id()
    access_token = _random_token()

    sql = """
        INSERT INTO orders (id, user_name, amount, status, item, note, access_token)
        VALUES (?, ?, ?, 'PENDING', ?, ?, ?)
    """
    await d1_db.execute_write(active_env, sql, (order_id, user_name, amount, item, note, access_token))
    order = await get_order_by_id(settings, order_id, env=active_env)
    logger.info(f"Tạo đơn hàng thành công: {order_id} cho {user_name}")
    return order


async def list_orders(settings: Settings, env: Any = None) -> List[dict]:
    """Admin lấy danh sách tất cả đơn hàng."""
    active_env = _get_active_env(env)
    return await d1_db.execute_query(active_env, "SELECT * FROM orders ORDER BY created_at DESC")


async def get_order_by_id(settings: Settings, order_id: str, env: Any = None) -> Optional[dict]:
    """Lấy đơn hàng theo ID (dùng nội bộ)."""
    active_env = _get_active_env(env)
    return await d1_db.execute_one(active_env, "SELECT * FROM orders WHERE id = ?", (order_id,))


async def get_order_for_customer(settings: Settings, order_id: str, access_token: str, env: Any = None) -> Optional[dict]:
    """Khách hàng lấy thông tin đơn hàng (kiểm tra access_token)."""
    active_env = _get_active_env(env)
    return await d1_db.execute_one(
        active_env,
        "SELECT * FROM orders WHERE id = ? AND access_token = ?",
        (order_id, access_token)
    )


async def update_order(settings: Settings, order_id: str,
                       user_name: Optional[str] = None, amount: Optional[float] = None,
                       item: Optional[str] = None, note: Optional[str] = None,
                       status: Optional[str] = None, env: Any = None) -> Optional[dict]:
    """Admin cập nhật thông tin đơn hàng."""
    active_env = _get_active_env(env)
    order = await get_order_by_id(settings, order_id, env=active_env)
    if not order:
        return None

    new_user = user_name if user_name is not None else order["user_name"]
    new_amount = amount if amount is not None else order["amount"]
    new_item = item if item is not None else order["item"]
    new_note = note if note is not None else order["note"]
    new_status = status if status is not None else order["status"]

    sql = """
        UPDATE orders
        SET user_name = ?, amount = ?, item = ?, note = ?, status = ?, updated_at = datetime('now')
        WHERE id = ?
    """
    await d1_db.execute_write(active_env, sql, (new_user, new_amount, new_item, new_note, new_status, order_id))
    logger.info(f"Cập nhật đơn hàng thành công: {order_id}")
    return await get_order_by_id(settings, order_id, env=active_env)


async def delete_order(settings: Settings, order_id: str, env: Any = None) -> bool:
    """Admin xóa đơn hàng (cascade xóa luôn transactions liên quan)."""
    active_env = _get_active_env(env)
    order = await get_order_by_id(settings, order_id, env=active_env)
    if not order:
        return False
    await d1_db.execute_write(active_env, "DELETE FROM orders WHERE id = ?", (order_id,))
    logger.info(f"Đã xóa đơn hàng: {order_id}")
    return True


# --- Transaction History ---

async def log_transaction(settings: Settings, order_id: str, transaction_id: str,
                          transfer_amount: float, content: str, raw_data: dict, env: Any = None) -> bool:
    """Ghi nhận lịch sử giao dịch từ webhook/IPN SePay. Trả về True nếu thành công, False nếu trùng lặp."""
    active_env = _get_active_env(env)
    # Check if transaction already exists
    existing = await d1_db.execute_one(
        active_env,
        "SELECT id FROM transactions WHERE transaction_id = ?",
        (transaction_id,)
    )
    if existing:
        logger.info(f"Giao dịch {transaction_id} đã tồn tại, bỏ qua trùng lặp.")
        return False

    raw_json = json.dumps(raw_data, ensure_ascii=False)
    sql = """
        INSERT INTO transactions (transaction_id, order_id, transfer_amount, content, raw_data)
        VALUES (?, ?, ?, ?, ?)
    """
    try:
        await d1_db.execute_write(active_env, sql, (transaction_id, order_id, transfer_amount, content, raw_json))
        logger.info(f"Đã ghi nhận giao dịch {transfer_amount}đ cho đơn hàng {order_id} (TX: {transaction_id})")
        return True
    except Exception as e:
        logger.error(f"Lỗi khi ghi lịch sử giao dịch ({order_id}): {e}")
        return False


async def get_transactions(settings: Settings, order_id: str, env: Any = None) -> List[dict]:
    """Admin lấy lịch sử giao dịch của một đơn hàng."""
    active_env = _get_active_env(env)
    return await d1_db.execute_query(
        active_env,
        "SELECT * FROM transactions WHERE order_id = ? ORDER BY created_at DESC",
        (order_id,)
    )


# --- Webhook ---

async def process_webhook(settings: Settings, sepay_data: dict, env: Any = None) -> Optional[Tuple[str, str]]:
    """Xử lý webhook từ SePay. Trả về (order_id, new_status) nếu xử lý thành công."""
    active_env = _get_active_env(env)
    content: str = sepay_data.get("content", "")
    transfer_amount = float(sepay_data.get("transferAmount", 0))
    transaction_id = str(sepay_data.get("id", ""))

    logger.info(f"Nhận webhook SePay: TX={transaction_id}, content='{content}', amount={transfer_amount}")

    order_id = None
    for part in content.upper().split():
        if part.startswith("SEVQR-"):
            order_id = part.replace("SEVQR-", "")
            break

    if not order_id:
        logger.warning(f"Không tìm thấy mã đơn hàng trong nội dung: {content}")
        return None

    order = await get_order_by_id(settings, order_id, env=active_env)
    if not order:
        logger.warning(f"Không tìm thấy đơn hàng: {order_id}")
        return None

    is_new_tx = await log_transaction(settings, order_id, transaction_id, transfer_amount, content, sepay_data, env=active_env)
    if not is_new_tx:
        logger.info(f"Giao dịch {transaction_id} đã được xử lý trước đó.")
        return None

    if order["status"] == "PAID":
        return (order_id, "PAID")

    if transfer_amount >= order["amount"]:
        await update_order(settings, order_id, status="PAID", env=active_env)
        new_status = "PAID"
    else:
        await update_order(settings, order_id, status="PARTIALLY_PAID", env=active_env)
        new_status = "PARTIALLY_PAID"

    return (order_id, new_status)


# =====================================================================
# SePay Payment Gateway — Checkout & IPN
# =====================================================================

_CHECKOUT_FIELD_ORDER = [
    "merchant",
    "operation",
    "payment_method",
    "order_amount",
    "currency",
    "order_invoice_number",
    "order_description",
    "customer_id",
    "success_url",
    "error_url",
    "cancel_url",
]


def _build_signature(fields: dict, secret_key: str) -> str:
    parts = []
    for field in _CHECKOUT_FIELD_ORDER:
        if field in fields and fields[field] is not None:
            parts.append(f"{field}={fields[field]}")
    message = ",".join(parts)
    raw = hmac.new(
        secret_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(raw).decode("utf-8")


async def build_checkout_fields(settings: Settings, order: dict, env: Any = None) -> dict:
    if not settings.sepay_merchant_id or not settings.sepay_secret_key:
        raise ValueError(
            "Thiếu cấu hình SePay Payment Gateway: SEPAY_MERCHANT_ID và SEPAY_SECRET_KEY là bắt buộc."
        )

    order_id = order["id"]
    access_token = order["access_token"]
    base = settings.frontend_base_url.rstrip("/")

    success_url = f"{base}/payment.html?id={order_id}&token={access_token}&payment=success"
    error_url   = f"{base}/payment.html?id={order_id}&token={access_token}&payment=error"
    cancel_url  = f"{base}/payment.html?id={order_id}&token={access_token}&payment=cancel"

    invoice_number = f"INV-{order_id}-{int(time.time())}"

    raw_fields = {
        "merchant":             settings.sepay_merchant_id,
        "operation":            "PURCHASE",
        "payment_method":       "BANK_TRANSFER",
        "order_amount":         str(int(order["amount"])),
        "currency":             "VND",
        "order_invoice_number": invoice_number,
        "order_description":    f"Thanh toan don hang {order_id}",
        "customer_id":          f"CUST-{order_id}",
        "success_url":          success_url,
        "error_url":            error_url,
        "cancel_url":           cancel_url,
    }

    fields = {k: raw_fields[k] for k in _CHECKOUT_FIELD_ORDER}
    fields["signature"] = _build_signature(fields, settings.sepay_secret_key)

    await _save_invoice_number(settings, order_id, invoice_number, env=env)
    return fields


def get_checkout_url(settings: Settings) -> str:
    if settings.sepay_env == "production":
        return "https://pay.sepay.vn/v1/checkout/init"
    return "https://pay-sandbox.sepay.vn/v1/checkout/init"


async def _save_invoice_number(settings: Settings, order_id: str, invoice_number: str, env: Any = None):
    active_env = _get_active_env(env)
    await d1_db.execute_write(
        active_env,
        "UPDATE orders SET invoice_number = ? WHERE id = ?",
        (invoice_number, order_id)
    )


async def _get_order_by_invoice(settings: Settings, invoice_number: str, env: Any = None) -> Optional[dict]:
    active_env = _get_active_env(env)
    return await d1_db.execute_one(
        active_env,
        "SELECT * FROM orders WHERE invoice_number = ?",
        (invoice_number,)
    )


async def process_ipn(settings: Settings, ipn_data: dict, env: Any = None) -> Optional[Tuple[str, str]]:
    active_env = _get_active_env(env)
    notification_type = ipn_data.get("notification_type", "")
    if notification_type != "ORDER_PAID":
        logger.info(f"IPN bỏ qua: notification_type={notification_type}")
        return None

    order_data = ipn_data.get("order", {})
    transaction_data = ipn_data.get("transaction", {})

    invoice_number = order_data.get("order_invoice_number", "")
    transaction_id = transaction_data.get("transaction_id", "")
    transfer_amount = float(transaction_data.get("transaction_amount", 0))

    if not invoice_number:
        logger.warning("IPN thiếu order_invoice_number, bỏ qua.")
        return None

    order = await _get_order_by_invoice(settings, invoice_number, env=active_env)
    if not order:
        logger.warning(f"Không tìm thấy đơn hàng cho invoice: {invoice_number}")
        return None

    order_id = order["id"]

    is_new = await log_transaction(
        settings, order_id, transaction_id,
        transfer_amount, invoice_number, ipn_data, env=active_env
    )
    if not is_new:
        logger.info(f"Giao dịch IPN {transaction_id} đã được xử lý trước đó (Replay bỏ qua).")
        return None

    if order["status"] == "PAID":
        return (order_id, "PAID")

    if transfer_amount >= order["amount"]:
        await update_order(settings, order_id, status="PAID", env=active_env)
        new_status = "PAID"
    else:
        await update_order(settings, order_id, status="PARTIALLY_PAID", env=active_env)
        new_status = "PARTIALLY_PAID"

    return (order_id, new_status)
