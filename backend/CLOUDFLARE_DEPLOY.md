# Hướng Dẫn Triển Khai Backend Lên Cloudflare Workers

Tài liệu này hướng dẫn các bước chi tiết để khởi tạo và deploy backend (Python FastAPI, Cloudflare D1, Cloudflare KV) lên Cloudflare Workers.

---

## 1. Yêu Cầu Ban Đầu (Prerequisites)

- Đã cài đặt Node.js (phiên bản 18+ hoặc 20+).
- Đăng nhập vào tài khoản Cloudflare trên máy thông qua Wrangler:
  ```bash
  npx wrangler login
  ```

---

## 2. Tạo Cơ Sở Dữ Liệu Cloudflare D1

1. **Tạo database D1:**
   ```bash
   cd backend
   npx wrangler d1 create fl-u01-db
   ```
   Lệnh trên sẽ in ra thông tin cấu hình tương tự như sau:
   ```toml
   [[d1_databases]]
   binding = "DB"
   database_name = "fl-u01-db"
   database_id = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
   ```
   👉 Sao chép `database_id` này và dán vào file [wrangler.toml](file:///d:/workspace/fl-u01-viet/backend/wrangler.toml).

2. **Khởi tạo schema bảng (orders, transactions):**
   - Áp dụng schema lên D1 trên Cloudflare:
     ```bash
     npx wrangler d1 execute fl-u01-db --remote --file=./schema.sql
     ```
   - (Tùy chọn) Áp dụng schema cho local development:
     ```bash
     npx wrangler d1 execute fl-u01-db --local --file=./schema.sql
     ```

---

## 3. Tạo Cloudflare KV Namespace (Lưu License Keys)

1. **Tạo namespace KV:**
   ```bash
   npx wrangler kv:namespace create KEYS_KV
   ```
   Lệnh sẽ in ra thông tin cấu hình:
   ```toml
   [[kv_namespaces]]
   binding = "KEYS_KV"
   id = "yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy"
   ```
   👉 Sao chép `id` này và dán vào mục `[[kv_namespaces]]` trong file [wrangler.toml](file:///d:/workspace/fl-u01-viet/backend/wrangler.toml).

---

## 4. Cấu Hình Biến Môi Trường và Secrets

### 4.1 Cho Local Development
Tạo file `.dev.vars` (dựa trên mẫu [.dev.vars.example](file:///d:/workspace/fl-u01-viet/backend/.dev.vars.example)):
```bash
cp .dev.vars.example .dev.vars
```
Chỉnh sửa file `.dev.vars` và điền các khóa bí mật của bạn (`PASS_ADMIN`, `SEPAY_MERCHANT_ID`, `SEPAY_SECRET_KEY`, `SEPAY_IPN_SECRET_KEY`, ...).

### 4.2 Cho Môi Trường Production Trên Cloudflare
Cấu hình các secrets bảo mật bằng lệnh wrangler:
```bash
npx wrangler secret put PASS_ADMIN
npx wrangler secret put SEPAY_MERCHANT_ID
npx wrangler secret put SEPAY_SECRET_KEY
npx wrangler secret put SEPAY_IPN_SECRET_KEY
npx wrangler secret put TELEGRAM_BOT_TOKEN
npx wrangler secret put TELEGRAM_CHAT_ID
npx wrangler secret put TELEGRAM_THREAD_ID
```
*(Wrangler sẽ nhắc bạn nhập giá trị bí mật một cách an toàn).*

---

## 5. Chạy Thử Nghiệm Local (Wrangler Dev)

Khởi động server dev mô phỏng Cloudflare Workers tại máy:
```bash
npx wrangler dev
```
Mặc định server sẽ chạy tại `http://localhost:8787`. Bạn có thể kiểm tra health check tại:
`http://localhost:8787/kaithhealthcheck`

---

## 6. Deploy Lên Cloudflare Workers

Khi đã sẵn sàng đưa lên môi trường thật:
```bash
npx wrangler deploy
```

Sau khi deploy thành công, Cloudflare sẽ cung cấp URL công khai (ví dụ: `https://backend-worker.<your-subdomain>.workers.dev`).

---

## 7. Cập Nhật Cấu Hình SePay IPN Webhook

Đăng nhập vào SePay Dashboard:
- Cổng thanh toán → Cấu hình → **IPN URL**:
  `https://<your-worker-url>/api/payment/ipn`
- Header xác thực: `X-Secret-Key` trùng với biến bí mật `SEPAY_IPN_SECRET_KEY`.
