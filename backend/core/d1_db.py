"""
Cloudflare D1 Database Adapter for Orders & Transactions.
Supports both Cloudflare D1 (in worker runtime) and local SQLite fallback.
"""

import asyncio
import json
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

LOCAL_DB_FILE = os.environ.get("LOCAL_SQLITE_PATH", "orders.sqlite3")


def _is_d1(env: Any) -> bool:
    return env is not None and hasattr(env, "DB") and env.DB is not None


def _get_local_connection():
    conn = sqlite3.connect(LOCAL_DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


async def init_db(env: Any = None):
    """Khởi tạo bảng trong D1 hoặc local SQLite nếu chưa tồn tại."""
    schema_sql = """
    CREATE TABLE IF NOT EXISTS orders (
        id TEXT PRIMARY KEY,
        user_name TEXT NOT NULL,
        amount REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING',
        item TEXT DEFAULT '',
        note TEXT DEFAULT '',
        access_token TEXT NOT NULL,
        invoice_number TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id TEXT UNIQUE,
        order_id TEXT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
        transfer_amount REAL NOT NULL,
        content TEXT DEFAULT '',
        raw_data TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    );
    """
    if _is_d1(env):
        try:
            # D1 batch or sequential exec
            for statement in schema_sql.strip().split(";"):
                stmt = statement.strip()
                if stmt:
                    res = env.DB.prepare(stmt)
                    if hasattr(res, "run"):
                        run_res = res.run()
                        if asyncio.iscoroutine(run_res) or hasattr(run_res, "__await__"):
                            await run_res
            logger.info("D1: Đã khởi tạo schema thành công.")
        except Exception as e:
            logger.error(f"D1: Lỗi khởi tạo schema: {e}")
            raise
    else:
        conn = _get_local_connection()
        try:
            conn.executescript(schema_sql)
            conn.commit()
            logger.info("Local SQLite: Đã khởi tạo schema thành công.")
        finally:
            conn.close()


def _convert_row(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    if hasattr(row, "to_py"):
        try:
            return dict(row.to_py())
        except Exception:
            pass
    if isinstance(row, sqlite3.Row):
        return dict(row)
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except Exception:
        return row


async def execute_query(env: Any, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    """Thực thi câu lệnh SELECT trả về danh sách dict."""
    if _is_d1(env):
        stmt = env.DB.prepare(sql)
        if params:
            stmt = stmt.bind(*params)
        res = stmt.all()
        if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
            res = await res
        results = getattr(res, "results", res)
        if hasattr(results, "to_py"):
            results = results.to_py()
        return [_convert_row(r) for r in results]
    else:
        conn = _get_local_connection()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [_convert_row(r) for r in rows]
        finally:
            conn.close()


async def execute_one(env: Any, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    """Thực thi câu lệnh SELECT trả về 1 bản ghi hoặc None."""
    if _is_d1(env):
        stmt = env.DB.prepare(sql)
        if params:
            stmt = stmt.bind(*params)
        res = stmt.first()
        if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
            res = await res
        return _convert_row(res)
    else:
        conn = _get_local_connection()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            row = cur.fetchone()
            return _convert_row(row)
        finally:
            conn.close()


async def execute_write(env: Any, sql: str, params: tuple = ()) -> bool:
    """Thực thi câu lệnh INSERT / UPDATE / DELETE."""
    if _is_d1(env):
        stmt = env.DB.prepare(sql)
        if params:
            stmt = stmt.bind(*params)
        res = stmt.run()
        if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
            await res
        return True
    else:
        conn = _get_local_connection()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
            return True
        finally:
            conn.close()
