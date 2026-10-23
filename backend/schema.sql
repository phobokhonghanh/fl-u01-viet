-- Cloudflare D1 Database Schema for Orders and Transactions

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
