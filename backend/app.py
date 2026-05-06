"""
Backend - Key & Payment API Service.
"""

import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers.keys import router as keys_router
from routers.payment import router as payment_router

app = FastAPI(title="Key & Payment API")
logger = logging.getLogger(__name__)

# --- Middleware ---
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
        init_db(settings)
    except Exception as e:
        logger.warning(f"Không thể khởi tạo payment DB: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
