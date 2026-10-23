"""
Backend - Key & Payment API Service.
Cloudflare Python Workers & ASGI Compatible.
"""

import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from routers.keys import router as keys_router
from routers.payment import router as payment_router
from core.env_context import set_current_env, reset_current_env

app = FastAPI(title="Key & Payment API")
logger = logging.getLogger(__name__)


# --- Cloudflare Worker Env Middleware ---
@app.middleware("http")
async def cloudflare_env_middleware(request: Request, call_next):
    """Trích xuất 'env' từ ASGI scope của Cloudflare Worker và gán vào request.state & context."""
    env = request.scope.get("env")
    request.state.env = env
    token = set_current_env(env)
    try:
        response = await call_next(request)
        return response
    finally:
        reset_current_env(token)


# --- CORS Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Routers ---
app.include_router(keys_router)
app.include_router(payment_router)


@app.get("/kaithhealthcheck")
async def health_check():
    return {"status": "ok"}


# --- Startup ---
@app.on_event("startup")
async def startup_event():
    """Khởi tạo database khi server khởi động."""
    try:
        from config.settings import Settings
        from core.payment_manager import init_db
        settings = Settings.from_env()
        await init_db(settings)
    except Exception as e:
        logger.warning(f"Không thể khởi tạo payment DB: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
