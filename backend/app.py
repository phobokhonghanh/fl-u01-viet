"""
Backend Worker - Key & Payment API Service.
Cloudflare Python Worker with native lightweight routing (Zero external dependencies).
"""

import asyncio
from datetime import datetime, timedelta
import json
import logging
from urllib.parse import urlparse, parse_qs
from workers import WorkerEntrypoint, Response

from config.settings import Settings
from core import key_manager, payment_manager
from core.telegram import send_telegram_notification
from models.schemas import (
    DEFAULT_KEY_PRODUCT,
    is_valid_key_product,
    normalize_key_product,
)

logger = logging.getLogger(__name__)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Secret-Key",
}


def json_response(data, status: int = 200, extra_headers: dict = None) -> Response:
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        **CORS_HEADERS,
        **(extra_headers or {}),
    }
    return Response(json.dumps(data, ensure_ascii=False), status=status, headers=headers)


def error_response(detail: str, status: int = 400) -> Response:
    return json_response({"detail": detail}, status=status)


async def read_json(request) -> dict:
    try:
        if hasattr(request, "json"):
            res = request.json()
            if hasattr(res, "__await__") or asyncio.iscoroutine(res):
                res = await res
            if isinstance(res, dict):
                return res
    except Exception:
        pass
    try:
        if hasattr(request, "text"):
            t = request.text()
            if hasattr(t, "__await__") or asyncio.iscoroutine(t):
                t = await t
            return json.loads(str(t))
    except Exception:
        pass
    return {}


def parse_version(v_str: str) -> tuple:
    if not v_str:
        return (0, 0)
    try:
        parts = tuple(int(x) for x in str(v_str).strip().split(".") if x.isdigit())
        return parts if parts else (0, 0)
    except Exception:
        return (0, 0)


def _serialize_order(order: dict) -> dict:
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


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        method = str(request.method).upper()

        # Handle CORS Preflight
        if method == "OPTIONS":
            return Response("", status=204, headers=CORS_HEADERS)

        url_str = str(request.url)
        parsed = urlparse(url_str)
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query)

        env = self.env
        settings = Settings.from_env(env)

        # Healthcheck
        if path == "/kaithhealthcheck" or path == "":
            return json_response({"status": "ok", "service": "key-payment-api"})

        try:
            # =================================================================
            # KEY MANAGEMENT ROUTER
            # =================================================================

            # Active / Validate License Key
            if path == "/api/key/active" and method == "POST":
                body = await read_json(request)
                key_str = body.get("key", "").strip()
                machine_id = body.get("machine_id")
                product = normalize_key_product(body.get("product"))
                client_version = body.get("client_version")

                if not is_valid_key_product(product):
                    return error_response("Invalid product", status=400)

                min_ver = parse_version(settings.min_client_version)
                client_ver = parse_version(client_version)
                if client_ver < min_ver:
                    prod_name = "AutoHDR" if product == "autohdr" else "Fotello"
                    return json_response({
                        "status": "error",
                        "valid": False,
                        "message": f"Phiên bản {prod_name} của bạn đã cũ. Vui lòng tải phiên bản mới nhất {settings.min_client_version} để tiếp tục sử dụng."
                    })

                record = await key_manager.async_verify_and_get_key(
                    settings.keys_filename, key_str, machine_id, product, env=env
                )
                if not record:
                    return error_response(
                        "Key is invalid, expired, used on another machine, or not allowed for this product",
                        status=403
                    )

                return json_response({
                    "status": "ok",
                    "valid": True,
                    "level": record.level,
                    "pricing": {
                        "lite": settings.price_lite,
                        "plus": settings.price_plus
                    }
                })

            # Admin: List all keys
            if path == "/api/admin/keys/list" and method == "POST":
                body = await read_json(request)
                if body.get("password") != settings.pass_admin:
                    return error_response("Unauthorized: Invalid admin password", status=401)

                keys = await key_manager.async_load_keys(settings.keys_filename, env=env)
                return json_response([k.to_dict() for k in keys])

            # Admin: Add / Update key
            if path == "/api/admin/keys/add" and method == "POST":
                body = await read_json(request)
                if body.get("password") != settings.pass_admin:
                    return error_response("Unauthorized: Invalid admin password", status=401)

                name = body.get("name", "").strip()
                days = body.get("days", 30)
                forever = body.get("forever", False)
                product = normalize_key_product(body.get("product", DEFAULT_KEY_PRODUCT))
                level = body.get("level", "lite")

                if not is_valid_key_product(product):
                    return error_response("Invalid product", status=400)

                expiry = None
                if not forever and days:
                    expiry_dt = datetime.utcnow() + timedelta(days=int(days))
                    expiry = expiry_dt.isoformat() + "Z"

                record, status, changes = await key_manager.async_add_or_update_key_by_name(
                    settings.keys_filename, name, expiry, product, level, env=env
                )

                # Send Telegram notification in background
                action_str = "CREATED" if status == "new" else ("UPDATED" if status == "updated" else "EXISTS")
                status_emoji = "🔑" if status == "new" else ("🔄" if status == "updated" else "ℹ️")
                msg = (
                    f"{status_emoji} <b>License Key {action_str}</b>\n"
                    f"• <b>Name</b>: <code>{record.name}</code>\n"
                    f"• <b>Product</b>: <code>{record.product}</code>\n"
                    f"• <b>Level</b>: <code>{record.level}</code>\n"
                    f"• <b>Expiry</b>: <code>{record.expires_at or 'Forever'}</code>"
                )
                asyncio.create_task(send_telegram_notification(msg, env=env))

                return json_response({"status": status, "record": record.to_dict()})

            # Admin: Delete key
            if path == "/api/admin/keys/delete" and method == "POST":
                body = await read_json(request)
                if body.get("password") != settings.pass_admin:
                    return error_response("Unauthorized: Invalid admin password", status=401)

                target_key = body.get("key", "").strip()
                success = await key_manager.async_delete_key(settings.keys_filename, target_key, env=env)
                if success:
                    msg = f"🗑️ <b>License Key DELETED</b>\n• <b>Identifier</b>: <code>{target_key}</code>"
                    asyncio.create_task(send_telegram_notification(msg, env=env))
                    return json_response({"status": "ok", "message": "Key deleted successfully"})
                return error_response("Key not found", status=404)

            # Admin: Reset single machine ID
            if path == "/api/admin/keys/reset" and method == "POST":
                body = await read_json(request)
                if body.get("password") != settings.pass_admin:
                    return error_response("Unauthorized: Invalid admin password", status=401)

                target_key = body.get("key", "").strip()
                record = await key_manager.async_reset_key_machine(settings.keys_filename, target_key, env=env)
                if not record:
                    return error_response("Key not found", status=404)

                msg = (
                    f"🔄 <b>License Key RESET</b>\n"
                    f"• <b>Name</b>: <code>{record.name}</code>\n"
                    f"• <b>Status</b>: <code>Machine ID Cleared</code>"
                )
                asyncio.create_task(send_telegram_notification(msg, env=env))
                return json_response({"status": "ok", "record": record.to_dict()})

            # Admin: Reset all machine IDs
            if path == "/api/admin/keys/reset-all" and method == "POST":
                body = await read_json(request)
                if body.get("password") != settings.pass_admin:
                    return error_response("Unauthorized: Invalid admin password", status=401)

                count = await key_manager.async_reset_all_keys_machine(settings.keys_filename, env=env)
                msg = f"🔄 <b>ALL License Keys RESET</b>\n• <b>Total Keys Reset</b>: <code>{count}</code>"
                asyncio.create_task(send_telegram_notification(msg, env=env))
                return json_response({"status": "ok", "message": f"Successfully reset {count} keys", "count": count})

            # Admin: Export keys
            if path == "/api/admin/keys/export" and method == "POST":
                body = await read_json(request)
                if body.get("password") != settings.pass_admin:
                    return error_response("Unauthorized: Invalid admin password", status=401)

                keys = await key_manager.async_load_keys(settings.keys_filename, env=env)
                content = json.dumps([k.to_dict() for k in keys], ensure_ascii=False, indent=2)
                headers = {
                    "Content-Disposition": f"attachment; filename=keys_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                }
                return json_response([k.to_dict() for k in keys], extra_headers=headers)

            # =================================================================
            # PAYMENT MANAGEMENT ROUTER
            # =================================================================

            # Admin: List orders
            if path == "/api/payment/admin/orders/list" and method == "POST":
                body = await read_json(request)
                if body.get("password") != settings.pass_admin:
                    return error_response("Unauthorized: Invalid admin password", status=401)

                orders = await payment_manager.list_orders(settings, env=env)
                return json_response([_serialize_order(o) for o in orders])

            # Admin: Create order
            if path == "/api/payment/admin/orders/create" and method == "POST":
                body = await read_json(request)
                if body.get("password") != settings.pass_admin:
                    return error_response("Unauthorized: Invalid admin password", status=401)

                user_name = body.get("user_name", "").strip()
                amount = float(body.get("amount", 0))
                item = body.get("item", "")
                note = body.get("note", "")

                order = await payment_manager.create_order(settings, user_name, amount, item, note, env=env)
                return json_response(_serialize_order(order))

            # Admin: Update order
            if path.startswith("/api/payment/admin/orders/update/") and method == "POST":
                order_id = path.replace("/api/payment/admin/orders/update/", "").strip()
                body = await read_json(request)
                if body.get("password") != settings.pass_admin:
                    return error_response("Unauthorized: Invalid admin password", status=401)

                status_val = body.get("status")
                valid_statuses = {"PENDING", "PAID", "PARTIALLY_PAID", "EXPIRED"}
                if status_val and status_val not in valid_statuses:
                    return error_response(f"Invalid status. Must be one of: {valid_statuses}", status=400)

                updated = await payment_manager.update_order(
                    settings, order_id,
                    user_name=body.get("user_name"),
                    amount=body.get("amount"),
                    item=body.get("item"),
                    note=body.get("note"),
                    status=status_val,
                    env=env
                )
                if not updated:
                    return error_response("Order not found", status=404)
                return json_response(_serialize_order(updated))

            # Admin: Delete order
            if path.startswith("/api/payment/admin/orders/delete/") and method == "POST":
                order_id = path.replace("/api/payment/admin/orders/delete/", "").strip()
                body = await read_json(request)
                if body.get("password") != settings.pass_admin:
                    return error_response("Unauthorized: Invalid admin password", status=401)

                if not await payment_manager.delete_order(settings, order_id, env=env):
                    return error_response("Order not found", status=404)
                return json_response({"status": "ok", "message": "Order deleted successfully"})

            # Admin: Get order transactions
            if path.startswith("/api/payment/admin/orders/") and path.endswith("/transactions") and method == "POST":
                order_id = path.replace("/api/payment/admin/orders/", "").replace("/transactions", "").strip()
                body = await read_json(request)
                if body.get("password") != settings.pass_admin:
                    return error_response("Unauthorized: Invalid admin password", status=401)

                txs = await payment_manager.get_transactions(settings, order_id, env=env)
                return json_response([_serialize_transaction(tx) for tx in txs])

            # Customer: Get order details
            if path.startswith("/api/payment/order/") and not path.endswith("/checkout") and not path.endswith("/events") and method == "GET":
                order_id = path.replace("/api/payment/order/", "").strip()
                token = (query.get("token") or [""])[0]

                order = await payment_manager.get_order_for_customer(settings, order_id, token, env=env)
                if not order:
                    return error_response("Order not found or invalid token", status=404)

                result = _serialize_order(order)
                if result.get("status") != "PAID":
                    result["item"] = None
                result.pop("access_token", None)
                return json_response(result)

            # Customer: Get checkout fields + URL
            if path.startswith("/api/payment/order/") and path.endswith("/checkout") and method == "GET":
                order_id = path.replace("/api/payment/order/", "").replace("/checkout", "").strip()
                token = (query.get("token") or [""])[0]

                order = await payment_manager.get_order_for_customer(settings, order_id, token, env=env)
                if not order:
                    return error_response("Order not found or invalid token", status=404)

                if order["status"] == "PAID":
                    return error_response("Order already paid", status=400)
                if order["status"] == "EXPIRED":
                    return error_response("Order has expired", status=400)

                fields = await payment_manager.build_checkout_fields(settings, order, env=env)
                checkout_url = payment_manager.get_checkout_url(settings)
                return json_response({"checkout_url": checkout_url, "fields": fields})

            # Customer: SSE Events stream
            if path.startswith("/api/payment/order/") and path.endswith("/events") and method == "GET":
                order_id = path.replace("/api/payment/order/", "").replace("/events", "").strip()
                token = (query.get("token") or [""])[0]

                order = await payment_manager.get_order_for_customer(settings, order_id, token, env=env)
                if not order:
                    return error_response("Order not found or invalid token", status=404)

                # Return current status in standard event format
                sse_data = f"data: {json.dumps({'status': order['status']})}\n\n"
                headers = {
                    "Content-Type": "text/event-stream",
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    **CORS_HEADERS,
                }
                return Response(sse_data, status=200, headers=headers)

            # SePay Legacy Webhook
            if path == "/api/payment/webhook/sepay" and method == "POST":
                data = await read_json(request)
                await payment_manager.process_webhook(settings, data, env=env)
                return json_response({"status": "ok"})

            # SePay IPN
            if path == "/api/payment/ipn" and method == "POST":
                ipn_secret = settings.sepay_ipn_secret_key
                x_secret_key = None
                try:
                    if hasattr(request.headers, "get"):
                        x_secret_key = request.headers.get("X-Secret-Key") or request.headers.get("x-secret-key")
                except Exception:
                    pass

                if ipn_secret and x_secret_key != ipn_secret:
                    return error_response("Unauthorized: Invalid IPN Secret Key", status=401)

                data = await read_json(request)
                await payment_manager.process_ipn(settings, data, env=env)
                return json_response({"success": True})

        except Exception as e:
            logger.error(f"Internal Error processing {path}: {e}")
            return error_response(f"Internal Error: {str(e)}", status=500)

        return error_response(f"Route not found: {method} {path}", status=404)

    async def on_fetch(self, request):
        return await self.fetch(request)
