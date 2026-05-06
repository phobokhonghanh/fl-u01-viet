"""
Payment Router - API endpoints cho hệ thống thanh toán SePay.
"""

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from config.settings import Settings
from core import payment_manager
from core.sse_manager import sse_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/payment", tags=["payment"])


def _check_admin(password: str, settings: Settings):
    """Kiểm tra quyền Admin qua password trong body, dùng chung PASS_ADMIN."""
    if password != settings.pass_admin:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid admin password")


def _serialize_order(order: dict) -> dict:
    """Chuyển đổi datetime sang ISO string để JSON serializable."""
    result = dict(order)
    for key in ("created_at", "updated_at"):
        if result.get(key) and hasattr(result[key], "isoformat"):
            result[key] = result[key].isoformat()
    return result


def _serialize_transaction(tx: dict) -> dict:
    result = dict(tx)
    if result.get("created_at") and hasattr(result["created_at"], "isoformat"):
        result["created_at"] = result["created_at"].isoformat()
    return result


# --- Admin Request Models ---

class AdminListOrdersRequest(BaseModel):
    password: str


class CreateOrderRequest(BaseModel):
    password: str
    user_name: str
    amount: float
    item: Optional[str] = ""
    note: Optional[str] = ""


class UpdateOrderRequest(BaseModel):
    password: str
    user_name: Optional[str] = None
    amount: Optional[float] = None
    item: Optional[str] = None
    note: Optional[str] = None
    status: Optional[str] = None


class DeleteOrderRequest(BaseModel):
    password: str


class AdminTransactionsRequest(BaseModel):
    password: str


# --- Admin Endpoints ---

@router.post("/admin/orders/list")
async def admin_list_orders(req: AdminListOrdersRequest):
    """Admin: Lấy danh sách toàn bộ đơn hàng."""
    settings = Settings.from_env()
    _check_admin(req.password, settings)
    orders = payment_manager.list_orders(settings)
    return [_serialize_order(o) for o in orders]


@router.post("/admin/orders/create")
async def admin_create_order(req: CreateOrderRequest):
    """Admin: Tạo đơn hàng mới."""
    settings = Settings.from_env()
    _check_admin(req.password, settings)
    order = payment_manager.create_order(settings, req.user_name, req.amount, req.item, req.note)
    return _serialize_order(order)


@router.post("/admin/orders/update/{order_id}")
async def admin_update_order(order_id: str, req: UpdateOrderRequest):
    """Admin: Cập nhật thông tin đơn hàng."""
    settings = Settings.from_env()
    _check_admin(req.password, settings)

    valid_statuses = {"PENDING", "PAID", "PARTIALLY_PAID", "EXPIRED"}
    if req.status and req.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {valid_statuses}")

    updated = payment_manager.update_order(
        settings, order_id,
        user_name=req.user_name, amount=req.amount,
        item=req.item, note=req.note, status=req.status,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Order not found")

    # Thông báo SSE cho khách đang theo dõi nếu status thay đổi
    if req.status:
        await sse_manager.notify(order_id, {"status": req.status})

    return _serialize_order(updated)


@router.post("/admin/orders/delete/{order_id}")
async def admin_delete_order(order_id: str, req: DeleteOrderRequest):
    """Admin: Xóa đơn hàng."""
    settings = Settings.from_env()
    _check_admin(req.password, settings)
    if not payment_manager.delete_order(settings, order_id):
        raise HTTPException(status_code=404, detail="Order not found")
    return {"status": "ok", "message": "Order deleted successfully"}


@router.post("/admin/orders/{order_id}/transactions")
async def admin_get_transactions(order_id: str, req: AdminTransactionsRequest):
    """Admin: Xem lịch sử giao dịch của một đơn hàng."""
    settings = Settings.from_env()
    _check_admin(req.password, settings)

    if not payment_manager.get_order_by_id(settings, order_id):
        raise HTTPException(status_code=404, detail="Order not found")

    txs = payment_manager.get_transactions(settings, order_id)
    return [_serialize_transaction(tx) for tx in txs]


# --- Customer Endpoints ---

@router.get("/order/{order_id}")
async def customer_get_order(order_id: str, token: str):
    """Khách hàng: Xem thông tin đơn hàng bằng ID + access_token."""
    settings = Settings.from_env()
    order = payment_manager.get_order_for_customer(settings, order_id, token)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found or invalid token")

    result = _serialize_order(order)
    if result["status"] not in ("PAID",):
        result["item"] = None
    result.pop("access_token", None)
    return result


@router.get("/order/{order_id}/events")
async def order_sse_events(order_id: str, token: str, request: Request):
    """
    SSE: Khách hàng đăng ký nhận sự kiện thời gian thực cho đơn hàng.
    Khi thanh toán thành công, server sẽ đẩy event xuống ngay lập tức.
    """
    settings = Settings.from_env()
    order = payment_manager.get_order_for_customer(settings, order_id, token)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found or invalid token")

    async def event_generator():
        # Gửi trạng thái hiện tại ngay khi kết nối
        yield {"data": json.dumps({"status": order["status"]})}

        # Nếu đã PAID rồi thì không cần giữ kết nối nữa
        if order["status"] == "PAID":
            return

        q = sse_manager.subscribe(order_id)
        try:
            while True:
                # Kiểm tra client đã ngắt kết nối chưa
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(q.get(), timeout=25)
                    yield {"data": json.dumps(data)}
                    # Nếu đã thanh toán đủ thì đóng stream
                    if data.get("status") == "PAID":
                        break
                except asyncio.TimeoutError:
                    # Gửi heartbeat để giữ kết nối
                    yield {"comment": "ping"}
        finally:
            sse_manager.unsubscribe(order_id, q)

    return EventSourceResponse(event_generator())


# --- SePay Legacy Webhook (giữ lại để tương thích) ---

@router.post("/webhook/sepay")
async def sepay_webhook(request: Request):
    """Nhận webhook từ SePay khi có giao dịch."""
    settings = Settings.from_env()

    # Kiểm tra secret nếu được cấu hình
    if settings.sepay_webhook_secret:
        auth_header = request.headers.get("Authorization", "")
        if auth_header != f"Apikey {settings.sepay_webhook_secret}":
            logger.warning("Webhook SePay bị từ chối: Sai Authorization header.")
            raise HTTPException(status_code=401, detail="Unauthorized webhook")

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    logger.info(f"Webhook SePay nhận được: {data}")
    result = payment_manager.process_webhook(settings, data)

    if result:
        order_id, new_status = result
        await sse_manager.notify(order_id, {"status": new_status})

    return {"status": "ok"}


# --- SePay Payment Gateway Checkout ---

@router.get("/order/{order_id}/checkout")
async def get_checkout_fields(order_id: str, token: str):
    """
    Trả về form fields + checkout URL để frontend tự động submit sang SePay.
    Bao gồm chữ ký HMAC-SHA256.

    Bad cases:
    - order không tồn tại / token sai: 404
    - đơn đã PAID: 400 (không cho checkout lại)
    - SEPAY_MERCHANT_ID/SECRET_KEY chưa cấu hình: 503
    - DB lỗi tạm thời: 503
    """
    settings = Settings.from_env()
    order = payment_manager.get_order_for_customer(settings, order_id, token)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found or invalid token")

    if order["status"] == "PAID":
        raise HTTPException(status_code=400, detail="Order already paid")

    if order["status"] == "EXPIRED":
        raise HTTPException(status_code=400, detail="Order has expired")

    try:
        fields = payment_manager.build_checkout_fields(settings, order)
        checkout_url = payment_manager.get_checkout_url(settings)
    except ValueError as e:
        logger.error(f"Lỗi cấu hình SePay PG: {e}")
        raise HTTPException(status_code=503, detail="Payment gateway is not configured. Please contact admin.")
    except Exception as e:
        logger.error(f"Lỗi tạo checkout fields cho đơn {order_id}: {e}")
        raise HTTPException(status_code=503, detail="Cannot initialize payment. Please try again later.")

    return {"checkout_url": checkout_url, "fields": fields}


# --- SePay IPN (Instant Payment Notification) ---

@router.post("/ipn")
async def sepay_ipn(
    request: Request,
    x_secret_key: Optional[str] = Header(None, alias="X-Secret-Key"),
    authorization: Optional[str] = Header(None)
):
    """
    Nhận IPN từ SePay Payment Gateway sau khi giao dịch hoàn thành.
    Endpoint này phải được đăng ký trong SePay Dashboard.

    Retry logic:
    - Trả 200: IPN đã xử lý xong (kể cả bỏ qua hợp lệ)
    - Trả 500: DB lỗi tạm thời → SePay sẽ tự retry IPN
    - Trả 400: dữ liệu không hợp lệ → SePay không retry (lỗi của client)
    """
    settings = Settings.from_env()

    # Xác thực IPN bằng X-Secret-Key header (theo tài liệu SePay Payment Gateway IPN)
    # Lấy secret key từ SePay Dashboard → Cổng thanh toán → Cấu hình → IPN
    # Biến môi trường: SEPAY_IPN_SECRET_KEY
    ipn_secret = settings.sepay_ipn_secret_key

    if ipn_secret:
        # Kiểm tra header X-Secret-Key gửi từ SePay
        if x_secret_key != ipn_secret:
            logger.warning(f"IPN: Xác thực thất bại. X-Secret-Key không khớp.")
            raise HTTPException(status_code=401, detail="Unauthorized: Invalid IPN Secret Key")
    else:
        # Chưa cấu hình IPN secret → bỏ qua xác thực (dùng khi test sandbox)
        logger.warning("IPN: SEPAY_IPN_SECRET_KEY chưa được cấu hình, bỏ qua xác thực (chỉ nên dùng khi test).")

    try:
        data = await request.json()
    except Exception:
        # Dữ liệu không parse được — không cần SePay retry
        logger.error("IPN: Không thể parse JSON payload.")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    logger.info(f"IPN SePay PG nhận được: {data}")

    try:
        result = payment_manager.process_ipn(settings, data)
    except Exception as e:
        # DB lỗi tạm thời — trả 500 để SePay retry sau
        logger.error(f"IPN xử lý thất bại (sẽ được retry): {e}")
        raise HTTPException(status_code=500, detail="Internal error, please retry")

    # Notify SSE nếu status thay đổi
    if result:
        order_id, new_status = result
        await sse_manager.notify(order_id, {"status": new_status})

    # Luôn trả 200 với các trường hợp bình thường (bỏ qua / đã xử lý)
    return {"success": True}
