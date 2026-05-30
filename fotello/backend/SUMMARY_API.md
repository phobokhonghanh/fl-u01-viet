# SUMMARY API

## Upload + Polling + Download

| STT | Step | Endpoint API | Domain URL | Request body | Response body | Description | Example |
|---:|---|---|---|---|---|---|---|
| 1 | Refresh token | `/v1/token?key=<firebase_api_key>` | `https://securetoken.googleapis.com` | `grant_type=refresh_token&refresh_token=<token>` | `{ "id_token": "...", "access_token": "...", "refresh_token": "..." }` | Làm mới Firebase token trước khi gọi Fotello/Firestore. | `POST https://securetoken.googleapis.com/v1/token?key=...` |
| 2 | Tạo upload record | `/v1/<createUpload>` | `https://api.fotello.co` | `{ "filename": "IMG_001.jpg", "teamId": "<team_id>" }` | `{ "id": "<upload_id>" }` | Tạo upload id cho từng ảnh local. | `POST https://api.fotello.co/v1/...` |
| 3 | Start resumable upload | `/v0/b/fotello-uploads/o?name=<upload_id>/<filename>` | `https://firebasestorage.googleapis.com` | `{ "contentType": "image/jpeg" }` | Header `X-Goog-Upload-URL` | Lấy upload URL từ Firebase Storage. | `POST .../o?name=abc/IMG_001.jpg` |
| 4 | Upload binary | `<X-Goog-Upload-URL>` | `https://firebasestorage.googleapis.com` | Binary image bytes | HTTP `2xx` | Upload file ảnh lên storage. | `POST <upload_url>` |
| 5 | Tạo listing | `/v1/<createListing>` | `https://api.fotello.co` | `{ "name": "...", "num_total_brackets": 3, "filenames": [...], "isDemoListing": false, "teamId": "<team_id>" }` | `{ "id": "<listing_id>" }` | Tạo listing/project mới. | `POST https://api.fotello.co/v1/...` |
| 6 | Tạo enhance | `/v1/<createEnhance>` | `https://api.fotello.co` | `{ "upload_ids": [...], "listing_id": "<listing_id>", "preferences": {...}, "teamId": "<team_id>" }` | `{ "id": "<enhance_id>" }` | Gửi bracket ảnh để Fotello xử lý enhance. | `POST https://api.fotello.co/v1/...` |
| 7 | Patch watermark | `/v1/projects/.../documents/enhances/<enhance_id>?updateMask.fieldPaths=<field>` | `https://firestore.googleapis.com` | `{ "fields": { "<is_watermark_field>": { "booleanValue": false } } }` | Firestore document | Tắt watermark sau khi tạo enhance. | `PATCH .../documents/enhances/abc?...` |
| 8 | Poll enhance doc | `/v1/projects/.../documents/enhances/<enhance_id>` | `https://firestore.googleapis.com` | Không có | Firestore document fields | Kiểm tra doc enhance đã có field ảnh chưa. Poll interval lấy từ `settings.json`. | `GET .../documents/enhances/abc` |
| 9 | Tải media | `/v0/b/<bucket>/o/<object>?alt=media` | `https://firebasestorage.googleapis.com` | Không có | Binary image bytes | Tải ảnh đã xử lý từ `gs://...`. | `GET https://firebasestorage.googleapis.com/v0/b/.../o/...?alt=media` |

## Manual Listing Download

| STT | Step | Endpoint API | Domain URL | Request body | Response body | Description | Example |
|---:|---|---|---|---|---|---|---|
| 1 | Refresh token | `/v1/token?key=<firebase_api_key>` | `https://securetoken.googleapis.com` | `grant_type=refresh_token&refresh_token=<token>` | `{ "id_token": "...", "access_token": "..." }` | Làm mới token trước khi tải listing. | `POST https://securetoken.googleapis.com/v1/token?key=...` |
| 2 | Query listings | `/v1/projects/.../documents:runQuery` | `https://firestore.googleapis.com` | `{ "structuredQuery": { "from": [{ "collectionId": "listings" }], "where": { ... }, "limit": 100 } }` | Array runQuery rows | Lấy danh sách listing theo team. | `POST .../documents:runQuery` |
| 3 | Query enhances | `/v1/projects/.../documents:runQuery` | `https://firestore.googleapis.com` | `{ "structuredQuery": { "from": [{ "collectionId": "enhances" }], "where": { "listingId": "<listing_id>" }, "limit": 200 } }` | Array runQuery rows | Lấy toàn bộ enhance thuộc listing. | `POST .../documents:runQuery` |
| 4 | Patch watermark | `/v1/projects/.../documents/enhances/<enhance_id>?updateMask.fieldPaths=<field>` | `https://firestore.googleapis.com` | `{ "fields": { "<is_watermark_field>": { "booleanValue": false } } }` | Firestore document | Patch watermark cho các enhance có ảnh. | `PATCH .../documents/enhances/abc?...` |
| 5 | Prepare ZIP | `/prepareDownload` | `https://us-central1-real-estate-firebase-4109e.cloudfunctions.net` | `{ "listing_id": "<listing_id>", "sections": ["photos"], "photo_formats": ["original"] }` | `{ "download_url": "https://..." }` | Chuẩn bị file ZIP cho listing. | `POST https://us-central1-real-estate-firebase-4109e.cloudfunctions.net/prepareDownload` |
| 6 | Download ZIP | `<download_url>` | URL trả về từ prepareDownload | Không có | ZIP bytes | Tải ZIP và extract từng ảnh. | `GET <download_url>` |
| 7 | Fallback get enhance doc | `/v1/projects/.../documents/enhances/<enhance_id>` | `https://firestore.googleapis.com` | Không có | Firestore document fields | Nếu ZIP fail, đọc từng enhance để lấy `gs://...`. | `GET .../documents/enhances/abc` |
| 8 | Fallback download media | `/v0/b/<bucket>/o/<object>?alt=media` | `https://firebasestorage.googleapis.com` | Không có | Binary image bytes | Tải từng ảnh từ Firebase Storage. | `GET https://firebasestorage.googleapis.com/v0/b/.../o/...?alt=media` |

## License Gate

| STT | Step | Endpoint API | Domain URL | Request body | Response body | Description | Example |
|---:|---|---|---|---|---|---|---|
| 1 | Active key | `/api/key/active` | `AUTOHDR_API_BASE` hoặc `https://u01-viet-backend.up.railway.app` | `{ "key": "<license_key>", "machine_id": "<machine_id>" }` | `{ "valid": true, "message": "..." }` | Kiểm tra license trước khi vào màn hình chính và trước job upload/download. | `POST https://u01-viet-backend.up.railway.app/api/key/active` |
