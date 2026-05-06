"""
Payment Manager - Quản lý đơn hàng thanh toán qua SePay Payment Gateway.
Kết nối với PostgreSQL (Leapcell DB) qua settings.database_url.
"""

import base64
import hashlib
import hmac
import json
import logging
import random
import string
import time
from typing import List, Optional, Tuple

import psycopg2
import psycopg2.extras
import psycopg2.errors

from config.settings import Settings

logger = logging.getLogger(__name__)


def _get_connection(settings: Settings):
    """Tạo kết nối tới PostgreSQL từ settings."""
    if not settings.database_url:
        raise ValueError("Biến môi trường DATABASE_URL chưa được cấu hình.")
    return psycopg2.connect(settings.database_url, cursor_factory=psycopg2.extras.RealDictCursor)


def init_db(settings: Settings):
    """Khởi tạo bảng orders và transactions nếu chưa tồn tại."""
    conn = _get_connection(settings)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id VARCHAR(8) PRIMARY KEY,
                    user_name VARCHAR(255) NOT NULL,
                    amount FLOAT NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
                    item TEXT DEFAULT '',
                    note TEXT DEFAULT '',
                    access_token VARCHAR(64) NOT NULL,
                    invoice_number VARCHAR(100),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id SERIAL PRIMARY KEY,
                    transaction_id VARCHAR(100) UNIQUE,
                    order_id VARCHAR(8) NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                    transfer_amount FLOAT NOT NULL,
                    content TEXT DEFAULT '',
                    raw_data TEXT DEFAULT '',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
        conn.commit()
        
        logger.info("Khởi tạo bảng orders và transactions thành công.")
    except Exception as e:
        conn.rollback()
        logger.error(f"Lỗi khi khởi tạo database: {e}")
        raise
    finally:
        conn.close()


def _random_id(length: int = 8) -> str:
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))


def _random_token(length: int = 32) -> str:
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))


# --- Orders CRUD ---

def create_order(settings: Settings, user_name: str, amount: float,
                 item: str = "", note: str = "") -> dict:
    """Admin tạo đơn hàng mới."""
    order_id = _random_id()
    access_token = _random_token()
    conn = _get_connection(settings)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO orders (id, user_name, amount, status, item, note, access_token)
                VALUES (%s, %s, %s, 'PENDING', %s, %s, %s)
                RETURNING *
            """, (order_id, user_name, amount, item, note, access_token))
            order = dict(cur.fetchone())
        conn.commit()
        logger.info(f"Tạo đơn hàng thành công: {order_id} cho {user_name}")
        return order
    except Exception as e:
        conn.rollback()
        logger.error(f"Lỗi khi tạo đơn hàng: {e}")
        raise
    finally:
        conn.close()


def list_orders(settings: Settings) -> List[dict]:
    """Admin lấy danh sách tất cả đơn hàng."""
    conn = _get_connection(settings)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM orders ORDER BY created_at DESC")
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_order_by_id(settings: Settings, order_id: str) -> Optional[dict]:
    """Lấy đơn hàng theo ID (dùng nội bộ)."""
    conn = _get_connection(settings)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM orders WHERE id = %s", (order_id,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def get_order_for_customer(settings: Settings, order_id: str, access_token: str) -> Optional[dict]:
    """Khách hàng lấy thông tin đơn hàng (kiểm tra access_token)."""
    conn = _get_connection(settings)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM orders WHERE id = %s AND access_token = %s",
                (order_id, access_token)
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def update_order(settings: Settings, order_id: str,
                 user_name: Optional[str] = None, amount: Optional[float] = None,
                 item: Optional[str] = None, note: Optional[str] = None,
                 status: Optional[str] = None) -> Optional[dict]:
    """Admin cập nhật thông tin đơn hàng."""
    order = get_order_by_id(settings, order_id)
    if not order:
        return None

    conn = _get_connection(settings)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE orders
                SET user_name = %s, amount = %s, item = %s, note = %s, status = %s, updated_at = NOW()
                WHERE id = %s
                RETURNING *
            """, (
                user_name if user_name is not None else order["user_name"],
                amount if amount is not None else order["amount"],
                item if item is not None else order["item"],
                note if note is not None else order["note"],
                status if status is not None else order["status"],
                order_id,
            ))
            row = cur.fetchone()
        conn.commit()
        logger.info(f"Cập nhật đơn hàng thành công: {order_id}")
        return dict(row) if row else None
    except Exception as e:
        conn.rollback()
        logger.error(f"Lỗi khi cập nhật đơn hàng {order_id}: {e}")
        raise
    finally:
        conn.close()


def delete_order(settings: Settings, order_id: str) -> bool:
    """Admin xóa đơn hàng (cascade xóa luôn transactions liên quan)."""
    conn = _get_connection(settings)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM orders WHERE id = %s RETURNING id", (order_id,))
            deleted = cur.fetchone()
        conn.commit()
        if deleted:
            logger.info(f"Đã xóa đơn hàng: {order_id}")
            return True
        return False
    except Exception as e:
        conn.rollback()
        logger.error(f"Lỗi khi xóa đơn hàng {order_id}: {e}")
        raise
    finally:
        conn.close()


# --- Transaction History ---

def log_transaction(settings: Settings, order_id: str, transaction_id: str,
                    transfer_amount: float, content: str, raw_data: dict) -> bool:
    """Ghi nhận lịch sử giao dịch từ webhook SePay. Trả về True nếu thành công, False nếu trùng lặp."""
    conn = _get_connection(settings)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO transactions (transaction_id, order_id, transfer_amount, content, raw_data)
                VALUES (%s, %s, %s, %s, %s)
            """, (transaction_id, order_id, transfer_amount, content, json.dumps(raw_data, ensure_ascii=False)))
        conn.commit()
        logger.info(f"Đã ghi nhận giao dịch {transfer_amount}đ cho đơn hàng {order_id} (TX: {transaction_id})")
        return True
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        logger.info(f"Giao dịch {transaction_id} đã tồn tại, bỏ qua trùng lặp.")
        return False
    except Exception as e:
        conn.rollback()
        logger.error(f"Lỗi khi ghi lịch sử giao dịch ({order_id}): {e}")
        raise
    finally:
        conn.close()


def get_transactions(settings: Settings, order_id: str) -> List[dict]:
    """Admin lấy lịch sử giao dịch của một đơn hàng."""
    conn = _get_connection(settings)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM transactions WHERE order_id = %s ORDER BY created_at DESC",
                (order_id,)
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# --- Webhook ---

def process_webhook(settings: Settings, sepay_data: dict) -> Optional[Tuple[str, str]]:
    """
    Xử lý webhook từ SePay.
    Trả về (order_id, new_status) nếu xử lý thành công, None nếu không tìm thấy đơn.
    """
    content: str = sepay_data.get("content", "")
    transfer_amount = float(sepay_data.get("transferAmount", 0))

    transaction_id = str(sepay_data.get("id", ""))
    
    logger.info(f"Nhận webhook SePay: TX={transaction_id}, content='{content}', amount={transfer_amount}")

    # Tìm mã đơn hàng trong nội dung chuyển khoản
    order_id = None
    for part in content.upper().split():
        if part.startswith("SEVQR-"):
            order_id = part.replace("SEVQR-", "")
            break

    if not order_id:
        logger.warning(f"Không tìm thấy mã đơn hàng trong nội dung: {content}")
        return None

    order = get_order_by_id(settings, order_id)
    if not order:
        logger.warning(f"Không tìm thấy đơn hàng: {order_id}")
        return None

    # Luôn ghi lại lịch sử giao dịch. Nếu trùng lặp (Replay), dừng xử lý
    is_new_tx = log_transaction(settings, order_id, transaction_id, transfer_amount, content, sepay_data)
    if not is_new_tx:
        logger.info(f"Giao dịch {transaction_id} đã được xử lý trước đó.")
        return None

    if order["status"] == "PAID":
        logger.info(f"Đơn hàng {order_id} đã được thanh toán trước đó. Giao dịch vẫn được ghi nhận.")
        return (order_id, "PAID")

    if transfer_amount >= order["amount"]:
        update_order(settings, order_id, status="PAID")
        new_status = "PAID"
        logger.info(f"Đơn hàng {order_id} đã thanh toán đủ ({transfer_amount}/{order['amount']}).")
    else:
        update_order(settings, order_id, status="PARTIALLY_PAID")
        new_status = "PARTIALLY_PAID"
        logger.warning(f"Đơn hàng {order_id} thanh toán thiếu ({transfer_amount}/{order['amount']}).")

    return (order_id, new_status)


# =====================================================================
# SePay Payment Gateway — Checkout & IPN
# =====================================================================

# Thứ tự fields bắt buộc theo tài liệu SePay để tạo chữ ký
_SIGN_FIELD_ORDER = [
    "order_amount", "merchant", "currency", "operation",
    "order_description", "order_invoice_number", "payment_method",
    "success_url", "error_url", "cancel_url",
]


def _build_signature(fields: dict, secret_key: str) -> str:
    """
    Tạo chữ ký HMAC-SHA256 theo đúng thứ tự quy định của SePay.
    Format: base64(HMAC-SHA256("field1=val1,field2=val2,...", secret_key))
    """
    parts = []
    for field in _SIGN_FIELD_ORDER:
        if field in fields and fields[field] is not None:
            parts.append(f"{field}={fields[field]}")
    message = ",".join(parts)
    logger.info(f"SePay signature string: {message}")
    raw = hmac.new(
        secret_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(raw).decode("utf-8")


def build_checkout_fields(settings: Settings, order: dict) -> dict:
    """
    Tạo dict các trường form để submit sang SePay checkout.
    Bao gồm chữ ký bảo mật và callback URLs.

    Raises:
        ValueError: Nếu thiếu SEPAY_MERCHANT_ID hoặc SEPAY_SECRET_KEY.
    """
    if not settings.sepay_merchant_id or not settings.sepay_secret_key:
        raise ValueError(
            "Thiếu cấu hình SePay Payment Gateway: SEPAY_MERCHANT_ID và SEPAY_SECRET_KEY là bắt buộc."
        )

    order_id = order["id"]
    access_token = order["access_token"]
    base = settings.frontend_base_url.rstrip("/")

    # Callback URLs kèm đầy đủ thông tin để nhận lại đơn hàng sau khi redirect
    success_url = f"{base}/payment.html?id={order_id}&token={access_token}&payment=success"
    error_url   = f"{base}/payment.html?id={order_id}&token={access_token}&payment=error"
    cancel_url  = f"{base}/payment.html?id={order_id}&token={access_token}&payment=cancel"

    # Dùng order_id làm invoice_number để IPN tìm lại đơn hàng
    invoice_number = f"INV-{order_id}-{int(time.time())}"

    # Thứ tự fields theo đúng template tài liệu SePay để form HTML không bị lỗi signature
    _FIELD_ORDER = [
        "merchant",
        "currency",
        "order_amount",
        "operation",
        "order_description",
        "order_invoice_number",
        "payment_method",
        "success_url",
        "error_url",
        "cancel_url",
    ]

    raw_fields = {
        "merchant":             settings.sepay_merchant_id,
        "currency":             "VND",
        "order_amount":         str(int(order["amount"])),
        "operation":            "PURCHASE",
        "order_description":    f"Thanh toan don hang {order_id}",
        "order_invoice_number": invoice_number,
        "payment_method":       "BANK_TRANSFER",
        "success_url":          success_url,
        "error_url":            error_url,
        "cancel_url":           cancel_url,
    }

    # Tạo dict có thứ tự (Python 3.7+ giữ insertion order) để signature và output đúng thứ tự
    fields = {k: raw_fields[k] for k in _FIELD_ORDER}
    fields["signature"] = _build_signature(fields, settings.sepay_secret_key)

    # Lưu invoice_number vào DB để IPN có thể mapping về order
    _save_invoice_number(settings, order_id, invoice_number)

    return fields


def get_checkout_url(settings: Settings) -> str:
    """Trả về URL của SePay checkout tuỳ theo môi trường."""
    if settings.sepay_env == "production":
        return "https://pay.sepay.vn/v1/checkout/init"
    return "https://pay-sandbox.sepay.vn/v1/checkout/init"


def _save_invoice_number(settings: Settings, order_id: str, invoice_number: str):
    """Lưu invoice_number vào orders để IPN tìm lại được. Retry 3 lần nếu DB lỗi tạm thời."""
    for attempt in range(3):
        try:
            conn = _get_connection(settings)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE orders SET invoice_number = %s WHERE id = %s",
                        (invoice_number, order_id)
                    )
                conn.commit()
                return
            finally:
                conn.close()
        except psycopg2.OperationalError as e:
            logger.warning(f"DB lỗi kết nối khi lưu invoice_number (lần {attempt+1}/3): {e}")
            if attempt == 2:
                raise
            import time as _time
            _time.sleep(1 * (attempt + 1))


def process_ipn(settings: Settings, ipn_data: dict) -> Optional[Tuple[str, str]]:
    """
    Xử lý IPN (Instant Payment Notification) từ SePay Payment Gateway.

    IPN JSON structure:
    {
      "notification_type": "ORDER_PAID",
      "order": { "order_invoice_number": "INV-XXXX-...", "order_status": "CAPTURED", ... },
      "transaction": { "transaction_id": "...", "transaction_amount": "100000", ... }
    }

    Trả về (order_id, new_status) hoặc None nếu không xử lý.

    Bad cases được xử lý:
    - notification_type != ORDER_PAID: bỏ qua, return None
    - invoice_number không tìm thấy đơn hàng: log warning, return None
    - Giao dịch trùng lặp (Replay): phát hiện qua UNIQUE transaction_id, return None
    - Đơn hàng đã PAID: bỏ qua cập nhật, return (order_id, PAID)
    - DB lỗi tạm thời: raise để FastAPI trả 500, SePay sẽ tự retry IPN
    """
    notification_type = ipn_data.get("notification_type", "")
    if notification_type != "ORDER_PAID":
        logger.info(f"IPN bỏ qua: notification_type={notification_type}")
        return None

    order_data = ipn_data.get("order", {})
    transaction_data = ipn_data.get("transaction", {})

    invoice_number = order_data.get("order_invoice_number", "")
    transaction_id = transaction_data.get("transaction_id", "")
    transfer_amount = float(transaction_data.get("transaction_amount", 0))

    logger.info(
        f"IPN nhận được: invoice={invoice_number}, tx={transaction_id}, amount={transfer_amount}"
    )

    if not invoice_number:
        logger.warning("IPN thiếu order_invoice_number, bỏ qua.")
        return None

    # Tìm đơn hàng qua invoice_number
    order = _get_order_by_invoice(settings, invoice_number)
    if not order:
        logger.warning(f"Không tìm thấy đơn hàng cho invoice: {invoice_number}")
        return None

    order_id = order["id"]

    # Ghi log giao dịch — phát hiện Replay qua UNIQUE transaction_id
    is_new = log_transaction(
        settings, order_id, transaction_id,
        transfer_amount, invoice_number, ipn_data
    )
    if not is_new:
        logger.info(f"Giao dịch IPN {transaction_id} đã được xử lý trước đó (Replay bỏ qua).")
        return None

    if order["status"] == "PAID":
        logger.info(f"Đơn hàng {order_id} đã PAID trước đó.")
        return (order_id, "PAID")

    # Kiểm tra số tiền
    if transfer_amount >= order["amount"]:
        update_order(settings, order_id, status="PAID")
        new_status = "PAID"
        logger.info(f"IPN: Đơn hàng {order_id} thanh toán đủ ({transfer_amount}/{order['amount']}).")
    else:
        update_order(settings, order_id, status="PARTIALLY_PAID")
        new_status = "PARTIALLY_PAID"
        logger.warning(
            f"IPN: Đơn hàng {order_id} thanh toán thiếu ({transfer_amount}/{order['amount']})."
        )

    return (order_id, new_status)


def _get_order_by_invoice(settings: Settings, invoice_number: str) -> Optional[dict]:
    """Tìm đơn hàng qua invoice_number (lưu khi tạo checkout)."""
    conn = _get_connection(settings)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM orders WHERE invoice_number = %s", (invoice_number,)
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()
