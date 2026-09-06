# Watermark Workflow Fixes Review & Verification Report

**Date**: 2026-09-06  
**Target Project**: Fotello Client Application (`/home/itc/workspace/freelancer/fl-u01-viet/fotello`)  
**Scope**: Direct interactive fix in Herdr pane (Watermark retry round stoppage, Vietnamese UI logging restoration, timestamp locking & timezone consistency, Step 03 upload integrity, and display name sanitization).

---

## 1. Executive Summary

All reported defects and review feedback from the user and Codex have been resolved, verified, and backed by a comprehensive automated test suite (74 passing tests):

1. **Watermark Same-Corner Retry**: Fixed early stoppage at attempt 2 where identical-corner watermark differences triggered `uncertain` -> `needs_review`, halting the retry loop. Verified with real-world artifacts (`a06da596`) and unit tests.
2. **Vietnamese Log Formatter**: Restored legacy step-by-step logs (`Step 01` to `Step 10`) and implemented a centralized Vietnamese log formatter (`format_cleaner_result_vn`) for both Workflow 1 (Coordinator) and Workflow 2 (Manual). Raw machine states (`need_variant`, `blocked`, `needs_review`, `DimensionMismatchError`, English tracebacks) are stripped from the UI while preserving technical details in local reports.
3. **Specific Dimension Mismatch Explanation**: Formatter parses and extracts conflicting dimensions (e.g., `3840x2558 vs 3840x2560`) and clearly states in Vietnamese that images cannot be stitched due to dimension discrepancy.
4. **Attempt-Specific Timestamp Locking**: Each retry round locks its own aware timestamp (`attempt["created_at"]` and `attempt["created_time"]`) at `submit(number)` time. Attempt 3 now reflects its actual run time rather than reusing the initial family manifest time.
5. **Timezone Consistency & Fixed-Offset Fallback**: Both `models.attempt_name` and `downloads.py` consistently format and parse with Vietnam local time (UTC+7, `Asia/Ho_Chi_Minh` with fixed offset `timezone(timedelta(hours=7))` fallback for Windows environments lacking `tzdata`). Roundtrip testing from aware ISO UTC to VN display name and parsing confirms zero-second delta.
6. **Deduplicated Listing Processing & Attempt Fallback**: Removed redundant duplicate code block in `fotello_list_listings`. Fallback to local manifest prioritizes the specific `attempt.created_at` / `attempt.created_time` for the listing's attempt round before falling back to `family.created_at`. Unparsed old listings without dates remain `"-"` and are never assigned `now`.
7. **Step 03 Upload Integrity**: `Step 03` verifies upload completeness. Cancelled uploads log `warn`, partial uploads (`< len(paths)`) log `error`, and `success` is only emitted when all files upload successfully.
8. **UI Display Name & Date Fallback**: Listings hide internal `[wm:family-id:attempt:chunk]` markers from the UI (`display_name`) while keeping the underlying identifier intact. Listings missing Firestore `createdAt` fall back to the local manifest's created timestamp instead of displaying `"-"`.

---

## 2. Root Cause Analysis & Live Evidence

### 2.1 Same-Corner Watermarks Halting Auto-Retry (Attempt 2 Stoppage)
- **Manifest Evidence**: In `/home/itc/Downloads/hdr-trick/test-/a06da596/reports/IMG_9942.json`, both images had watermarks in corner `BR`. The pixel delta was 124 px (below `required_group_pixels` of 358 px), causing `active_corners` to be empty `[]`.
- **Root Cause**: `compare_variant_pair` fell through to `uncertain`. In `clean_output`, an uncertain comparison produced `status = "needs_review"` (or left `need_variant` unselected), so `coordinator.py` found `selected = []` in its retry loop and exited with a partial summary instead of uploading round 3.
- **Fix**: In `cleaner.py`, when pixel differences are confined to at most one physical corner (`len(active_corners) <= 1 and len(group_corners) <= 1`), `compare_variant_pair` reliably reports `duplicate`. In `clean_output`, inconclusive comparisons return `status = "need_variant"` when no complete candidate exists, prompting the coordinator to proceed to attempt 3.

### 2.2 Family Created Time Shared Across Attempts & Timezone Skew
- **Root Cause**:
  - `coordinator.py` called `attempt_name` with `manifest.get("created_time")`. Consequently, attempt 3 carried attempt 1's timestamp.
  - `models.attempt_name` used machine local time (`dt.astimezone()`), while `downloads.py` parsed using `VN_TZ`. On machines configured in UTC (servers/Docker), this resulted in a 7-hour discrepancy between the remote name and sort order.
- **Fix**:
  - `submit(number, selected)` records `now_dt` in `attempt["created_at"]` and `attempt["created_time"]` formatted with `_to_vn_datetime`.
  - Both `models.py` and `downloads.py` share a unified Vietnam timezone definition (+07) with fallback to `timezone(timedelta(hours=7))` if system tzdata is unavailable.

### 2.3 English & Machine Code Leakage to UI
- **Root Cause**:
  - `coordinator.py` logged `{st} — {rs}` in its fallback branch, causing `blocked: DimensionMismatchError: ...` to display directly to users.
  - `manual.py` logged `f"{group['output_name']}: {result['status']} — {result.get('reason', '')}"`.
- **Fix**: Built `format_cleaner_result_vn(output_name, result, with_step=True/False)` in `cleaner.py` and adopted it across both `coordinator.py` and `manual.py`.

### 2.4 Duplicate Code Block in `fotello_list_listings` & Inaccurate Manifest Fallback
- **Root Cause**: `fotello_list_listings` had two consecutive duplicate blocks reading `created_at` and `created_sort`. Furthermore, manifest fallback only checked top-level `manifest.created_at`, ignoring the actual attempt round timestamp.
- **Fix**: Removed the duplicate block. Enhanced manifest fallback to find the specific attempt matching `marker["attempt"]` in `manifest["attempts"]`, prioritizing `attempt.created_at` / `attempt.created_time` first. Untimed legacy listings never receive `datetime.now()`.

---

## 3. Files Modified & Created

| File | Change Type | Summary of Changes |
|---|---|---|
| `backend/watermark_workflow/cleaner.py` | Modified | • Refined `compare_variant_pair` corner grouping and duplicate classification.<br>• Non-preview inconclusive attempts map to `need_variant` for auto-retries.<br>• Added centralized `format_cleaner_result_vn` supporting WF1 and WF2. |
| `backend/watermark_workflow/coordinator.py` | Modified | • Locks `attempt["created_at"]` and `attempt["created_time"]` per attempt in `submit` using `_to_vn_datetime`.<br>• Uses attempt-specific created time for listing chunks.<br>• Step 03 checks for cancellation (`warn`) and incomplete uploads (`error`).<br>• Connects cleaner output logging to `format_cleaner_result_vn`. |
| `backend/watermark_workflow/manual.py` | Modified | • Replaced raw status and English prose logging with `format_cleaner_result_vn(..., with_step=False)`. |
| `backend/watermark_workflow/models.py` | Modified | • Added `VN_TZ` and `_to_vn_datetime` with fixed-offset UTC+7 fallback.<br>• Enhanced `_ATTEMPT_MARKER` and `_LEGACY_LISTING_MARKER` regexes.<br>• Updated `attempt_name` and `parse_attempt_name(..., full=True)` to extract `display_name`, `timestamp`, and `part_chunk`.<br>• Fixed `part_prefix` logic when `total_chunks > 1`. |
| `backend/watermark_workflow/store.py` | Modified | • Added `find_by_family(family_id, team_id)` for local manifest lookup when Firestore doc lacks `createdAt`. |
| `backend/downloads.py` | Modified | • Removed duplicate parsing block in `fotello_list_listings`.<br>• Standardized `VN_TZ` with fixed offset UTC+7 fallback for Windows compatibility.<br>• Manifest fallback prioritizes `attempt.created_at` / `created_time` over `family.created_at`.<br>• Added `display_name` to listing dicts, hiding UUID markers from UI.<br>• Prevents setting `now` for untimed old listings. |
| `backend/service.py` | Modified | • Restored `Step 01: Kiểm tra input - tìm thấy {total_images} ảnh hợp lệ.` log. |
| `ui/app.js` | Modified | • Renders `listing.display_name || listing.name` for clean title display without UUID marker. |
| `tests/test_watermark_fixes.py` | Created | • 13 automated unit tests verifying Vietnamese formatter, timestamp locking, UTC-to-VN roundtrip, attempt fallback prioritization, naming, and Step 03 upload integrity. |

---

## 4. Vietnamese Log Mapping Matrix

| Event / Outcome | Coordinator (WF1) Log | Manual (WF2) Log | Level |
|---|---|---|---|
| **Cleaned Success** | `Step 10: Ghép thành công - <Tên ảnh>` | `<Tên ảnh>: Ghép thành công, đã xóa sạch watermark.` | `success` |
| **Duplicate Watermark (Same Corner)** | `Step 09: Watermark trùng góc - cần lấy thêm biến thể cho <Tên ảnh>` | `<Tên ảnh>: Watermark trùng góc - cần thêm ảnh biến thể khác góc để ghép.` | `info` |
| **Uncertain Watermark** | `Step 09: Chưa đủ cặp góc sạch - cần lấy thêm biến thể cho <Tên ảnh>` | `<Tên ảnh>: Chưa đủ cặp góc sạch - cần thêm ảnh biến thể khác để ghép.` | `info` |
| **Seam Warning (Preview kept)** | `Step 10: Cần kiểm tra đường nối (seam) - <Tên ảnh>: Ảnh xem trước đã tạo nhưng có đường nối/chất lượng chưa tối ưu.` | `<Tên ảnh>: Cần kiểm tra đường nối (seam) - Đã tạo ảnh xem trước nhưng chất lượng chưa tối ưu.` | `warn` |
| **Dimension Mismatch** | `Step 10: Kích thước ảnh không khớp (<W1xH1> vs <W2xH2>) - <Tên ảnh>: Không thể ghép ảnh.` | `<Tên ảnh>: Kích thước ảnh không khớp (<W1xH1> vs <W2xH2>) - Không thể ghép ảnh.` | `error` |
| **Source Image Mismatch** | `Step 10: Ảnh gốc không khớp nội dung - <Tên ảnh>: Không thể ghép ảnh.` | `<Tên ảnh>: Ảnh gốc không khớp nội dung - Không thể ghép ảnh.` | `error` |
| **Corrupted File** | `Step 10: Tệp ảnh bị lỗi hoặc không đọc được - <Tên ảnh>: Không thể xử lý.` | `<Tên ảnh>: Tệp ảnh bị lỗi hoặc không đọc được - Không thể xử lý.` | `error` |
| **Cancelled Step 03** | `Step 03: Upload lượt <NN> bị dừng - đã tải <X>/<Y> ảnh.` | N/A | `warn` |
| **Partial Step 03** | `Step 03: Upload lượt <NN> không hoàn tất - chỉ tải được <X>/<Y> ảnh.` | N/A | `error` |
| **Complete Step 03** | `Step 03: Hoàn tất upload lượt <NN> - <X>/<Y> ảnh.` | N/A | `success` |

---

## 5. Verification & Test Results

### 5.1 Automated Test Suite
- Command executed: `rtk proxy .venv/bin/python -m unittest discover -s tests -v`
- **Result**: `Ran 74 tests in 46.226s. OK.` (100% passing)

### 5.2 Syntax and Compilation Checks
- `rtk proxy node --check ui/app.js`: Clean exit (code 0).
- `rtk proxy .venv/bin/python -m compileall backend`: Clean exit (code 0, all files compiled).
- `rtk git diff --check`: Clean exit (no trailing whitespace or conflict markers).

---

## 6. Safety & Operational Boundaries

1. **No Live External API Calls**: All tests used injected fake/mock providers. No real listings or enhance jobs were submitted to live Fotello servers.
2. **Timezone Roundtrip Integrity**: UTC ISO (`2026-09-06T06:30:00+00:00`) translates to Vietnam time (`06 09, 2026 13:30`), which parses back with a timestamp difference of exactly 0.0 seconds regardless of system timezone.
3. **Backward Compatibility**: `parse_attempt_name(name, full=False)` maintains its exact 4-key return signature (`family_id`, `attempt`, `chunk`, `prefix`) for existing callers while enabling full UI enrichment when `full=True`.
4. **Workspace Integrity**: No uncommitted working files were reset or destroyed.
