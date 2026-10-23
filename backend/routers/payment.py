"""
Payment Router - API endpoints cho hệ thống thanh toán SePay.
Tương thích với Cloudflare Workers (D1 SQLite, serverless stateless SSE).
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
from core.env_context import get_current_env

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/payment", tags=["payment"])


def _get_request_env(request: Optional[Request] = None):
    if request is not None and hasattr(request.state, "env") and request.state.env is not None:
        return request.state.env
    return get_current_env()


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
    for key in ("created_at",):
        if result.get(key) and hasattr(result[key], "isoformat"):
            result[key] = result[key].isoformat()
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
async def admin_list_orders(req: AdminListOrdersRequest, request: Request):
    """Admin: Lấy danh sách toàn bộ đơn hàng."""
    env = _get_request_env(request)
    settings = Settings.from_env(env)
    _check_admin(req.password, settings)
    orders = await payment_manager.list_orders(settings, env=env)
    return [_serialize_order(o) for o in orders]


@router.post("/admin/orders/create")
async def admin_create_order(req: CreateOrderRequest, request: Request):
    """Admin: Tạo đơn hàng mới."""
    env = _get_request_env(request)
    settings = Settings.from_env(env)
    _check_admin(req.password, settings)
    order = await payment_manager.create_order(settings, req.user_name, req.amount, req.item, req.note, env=env)
    return _serialize_order(order)


@router.post("/admin/orders/update/{order_id}")
async def admin_update_order(order_id: str, req: UpdateOrderRequest, request: Request):
    """Admin: Cập nhật thông tin đơn hàng."""
    env = _get_request_env(request)
    settings = Settings.from_env(env)
    _check_admin(req.password, settings)

    valid_statuses = {"PENDING", "PAID", "PARTIALLY_PAID", "EXPIRED"}
    if req.status and req.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {valid_statuses}")

    updated = await payment_manager.update_order(
        settings, order_id,
        user_name=req.user_name, amount=req.amount,
        item=req.item, note=req.note, status=req.status,
        env=env
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Order not found")

    return _serialize_order(updated)


@router.post("/admin/orders/delete/{order_id}")
async def admin_delete_order(order_id: str, req: DeleteOrderRequest, request: Request):
    """Admin: Xóa đơn hàng."""
    env = _get_request_env(request)
    settings = Settings.from_env(env)
    _check_admin(req.password, settings)
    if not await payment_manager.delete_order(settings, order_id, env=env):
        raise HTTPException(status_code=404, detail="Order not found")
    return {"status": "ok", "message": "Order deleted successfully"}


@router.post("/admin/orders/{order_id}/transactions")
async def admin_get_transactions(order_id: str, req: AdminTransactionsRequest, request: Request):
    """Admin: Xem lịch sử giao dịch của một đơn hàng."""
    env = _get_request_env(request)
    settings = Settings.from_env(env)
    _check_admin(req.password, settings)

    if not await payment_manager.get_order_by_id(settings, order_id, env=env):
        raise HTTPException(status_code=404, detail="Order not found")

    txs = await payment_manager.get_transactions(settings, order_id, env=env)
    return [_serialize_transaction(tx) for tx in txs]


# --- Customer Endpoints ---

@router.get("/order/{order_id}")
async def customer_get_order(order_id: str, token: str, request: Request):
    """Khách hàng: Xem thông tin đơn hàng bằng ID + access_token."""
    env = _get_request_env(request)
    settings = Settings.from_env(env)
    order = await payment_manager.get_order_for_customer(settings, order_id, token, env=env)
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
    Định kỳ poll D1 mỗi 2.5s để stream event cập nhật mà không cần Durable Objects.
    """
    env = _get_request_env(request)
    settings = Settings.from_env(env)
    order = await payment_manager.get_order_for_customer(settings, order_id, token, env=env)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found or invalid token")

    async def event_generator():
        # Gửi trạng thái ban đầu
        current_status = order["status"]
        yield {"data": json.dumps({"status": current_status})}

        if current_status == "PAID":
            return

        last_status = current_status
        # Giữ kết nối tối đa 60 vòng x 2.5s ~ 2.5 phút trước khi client tự reconnect
        for _ in range(60):
            if await request.is_disconnected():
                break

            await asyncio.sleep(2.5)

            # Lấy trạng thái mới nhất từ D1
            latest_order = await payment_manager.get_order_for_customer(settings, order_id, token, env=env)
            if not latest_order:
                break

            status_now = latest_order["status"]
            if status_now != last_status:
                last_status = status_now
                yield {"data": json.dumps({"status": status_now})}
                if status_now == "PAID":
                    break
            else:
                # Heartbeat ping để giữ kết nối qua edge proxy
                yield {"comment": "ping"}

    return EventSourceResponse(event_generator())


# --- SePay Legacy Webhook ---

@router.post("/webhook/sepay")
async def sepay_webhook(request: Request):
    """Nhận webhook từ SePay khi có giao dịch."""
    env = _get_request_env(request)
    settings = Settings.from_env(env)

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    logger.info(f"Webhook SePay nhận được: {data}")
    await payment_manager.process_webhook(settings, data, env=env)
    return {"status": "ok"}


# --- SePay Payment Gateway Checkout ---

@router.get("/order/{order_id}/checkout")
async def get_checkout_fields(order_id: str, token: str, request: Request):
    """
    Trả về form fields + checkout URL để frontend tự động submit sang SePay.
    Bao gồm chữ ký HMAC-SHA256.
    """
    env = _get_request_env(request)
    settings = Settings.from_env(env)
    order = await payment_manager.get_order_for_customer(settings, order_id, token, env=env)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found or invalid token")

    if order["status"] == "PAID":
        raise HTTPException(status_code=400, detail="Order already paid")

    if order["status"] == "EXPIRED":
        raise HTTPException(status_code=400, detail="Order has expired")

    try:
        fields = await payment_manager.build_checkout_fields(settings, order, env=env)
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
):
    """
    Nhận IPN từ SePay Payment Gateway sau khi giao dịch hoàn thành.
    Endpoint này phải được đăng ký trong SePay Dashboard.
    """
    env = _get_request_env(request)
    settings = Settings.from_env(env)

    ipn_secret = settings.sepay_ipn_secret_key
    if ipn_secret:
        if x_secret_key != ipn_secret:
            logger.warning("IPN: Xác thực thất bại. X-Secret-Key không khớp.")
            raise HTTPException(status_code=401, detail="Unauthorized: Invalid IPN Secret Key")
    else:
        logger.warning("IPN: SEPAY_IPN_SECRET_KEY chưa được cấu hình, bỏ qua xác thực (chỉ nên dùng khi test).")

    try:
        data = await request.json()
    except Exception:
        logger.error("IPN: Không thể parse JSON payload.")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    logger.info(f"IPN SePay PG nhận được: {data}")

    try:
        await payment_manager.process_ipn(settings, data, env=env)
    except Exception as e:
        logger.error(f"IPN xử lý thất bại (sẽ được retry): {e}")
        raise HTTPException(status_code=500, detail="Internal error, please retry")

    return {"success": True}
