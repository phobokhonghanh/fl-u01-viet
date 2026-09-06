"""Automatic variant generation with immutable source groups and selective retries."""
from __future__ import annotations

import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from .cleaner import format_cleaner_result_vn
from .models import attempt_name, summary, _to_vn_datetime
from .store import ManifestStore


def run_auto(manifest, *, upload, create_listing, create_enhance, check_ready,
             download, clean, max_workers=4, chunk_size=30, poll_timeout=600,
             poll_interval=None, sleep=None, is_cancelled=None, log=None,
             progress_fn=None, count_fn=None, summary_fn=None):
    """Dependencies are injected so tests never need a live Fotello account.

    Every attempt uploads its own inputs: cross-listing upload-ID reuse is not
    assumed. Creation requests are issued once and checkpointed before sending.
    """
    cancelled = is_cancelled or (lambda: False)
    log = log or (lambda *args: None)
    progress_fn = progress_fn or (lambda *args: None)
    count_fn = count_fn or (lambda **kwargs: None)
    summary_fn = summary_fn or (lambda value: None)
    sleep = sleep or time.sleep
    poll_interval = poll_interval or (lambda attempt, ready: 5)
    store = ManifestStore(manifest["output_dir"])
    groups = {g["output_id"]: g for g in manifest["groups"]}
    uploaded_count = 0

    def checkpoint():
        store.save(manifest)
        result = summary(manifest)
        count_fn(uploaded=uploaded_count, downloaded=result["downloaded_count"])
        progress_fn(result["cleaned_count"], result["target_count"])
        summary_fn(result)
        return result

    def block(group, reason):
        if group["status"] != "cleaned":
            group.update(status="blocked", reason=str(reason))
        log(f'{group["output_name"]}: {reason}', "error")

    def submit(number, selected):
        nonlocal uploaded_count
        now_dt = datetime.now(timezone.utc)
        attempt_time_str = _to_vn_datetime(now_dt).strftime("%d %m, %Y %H:%M")
        attempt = {
            "number": number,
            "created_at": now_dt.isoformat(),
            "created_time": attempt_time_str,
            "listings": [],
            "status": "uploading",
        }
        manifest["attempts"].append(attempt)
        checkpoint()
        # Only the coordinator mutates manifests; workers return upload results.
        uploaded = {}
        paths = [p for g in selected for p in g["input_paths"]]
        fingerprints = {path: identity for g in selected
                        for path, identity in g.get("input_fingerprints", {}).items()}
        log(f"Step 02: Bắt đầu tải lên lượt {number:02d} - {len(selected)} nhóm / {len(paths)} ảnh gốc.", "info")

        def do_upload(path):
            if cancelled():
                return None
            source = Path(path)
            expected = fingerprints.get(str(source.resolve()))
            if expected is not None:
                current = source.stat()
                if (expected.get("size"), expected.get("mtime_ns")) != (current.st_size, current.st_mtime_ns):
                    raise ValueError(f"Ảnh gốc đã thay đổi từ lúc bắt đầu: {source.name}")
            log(f"Step 02: Đang tải lên - {source.name}", "info")
            return upload(source)

        failures = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(do_upload, path): path for path in paths}
            for future in as_completed(futures):
                path = futures[future]
                try:
                    upload_id = future.result()
                    if upload_id:
                        uploaded[path] = upload_id
                        uploaded_count += 1
                    elif not cancelled():
                        failures[path] = "Upload không trả về ID"
                except Exception as exc:
                    failures[path] = str(exc)
                attempt["uploads"] = dict(uploaded)
                checkpoint()
        if cancelled():
            log(f"Step 03: Upload lượt {number:02d} bị dừng - đã tải {len(uploaded)}/{len(paths)} ảnh.", "warn")
        elif len(uploaded) < len(paths):
            log(f"Step 03: Upload lượt {number:02d} không hoàn tất - chỉ tải được {len(uploaded)}/{len(paths)} ảnh.", "error")
        else:
            log(f"Step 03: Hoàn tất upload lượt {number:02d} - {len(uploaded)}/{len(paths)} ảnh.", "success")
        eligible = []
        for group in selected:
            missing = [p for p in group["input_paths"] if p not in uploaded]
            if missing and not cancelled():
                block(group, f'Upload thiếu input: {Path(missing[0]).name}: {failures.get(missing[0], "")}' )
            elif not missing and group["status"] == "need_variant":
                eligible.append(group)
        total_chunks = max(1, (len(eligible) + chunk_size - 1) // chunk_size)
        for offset in range(0, len(eligible), chunk_size):
            if cancelled():
                break
            chunk = eligible[offset:offset + chunk_size]
            chunk_number = offset // chunk_size + 1
            name = attempt_name(
                manifest["prefix"],
                number,
                manifest["family_id"],
                chunk=chunk_number,
                total_chunks=total_chunks,
                created_at=attempt.get("created_time") or manifest.get("created_time"),
            )
            listing = {"chunk": chunk_number, "name": name, "enhances": [],
                       "output_ids": [g["output_id"] for g in chunk],
                       "status": "submission_unknown"}
            attempt["listings"].append(listing)
            checkpoint()
            log(f"Step 04: Tạo listing lượt {number:02d} đợt {chunk_number}/{total_chunks}", "info")
            try:
                listing_id = create_listing(name, chunk)
                if not listing_id:
                    raise RuntimeError("Tạo listing không trả về ID; cần đối soát trước khi gửi lại")
                listing.update(listing_id=listing_id, status="created")
                log(f"Step 05: Tạo listing lượt {number:02d} đợt {chunk_number} thành công - {str(listing_id)[:8]} / {len(chunk)} brackets.", "success")
                checkpoint()
            except Exception as exc:
                listing["error"] = str(exc)
                for group in chunk:
                    block(group, f"Tạo listing chưa xác định kết quả: {exc}")
                checkpoint()
                continue
            log(f"Step 06: Kích hoạt xử lý lượt {number:02d} đợt {chunk_number}...", "info")
            for group in chunk:
                if cancelled():
                    break
                record = {"output_id": group["output_id"], "status": "submission_unknown"}
                listing["enhances"].append(record)
                checkpoint()
                try:
                    ids = [uploaded[p] for p in group["input_paths"]]
                    enhance_id = create_enhance(listing_id, ids)
                    if not enhance_id:
                        raise RuntimeError("Tạo enhance không trả về ID; cần đối soát")
                    record.update(enhance_id=enhance_id, status="pending")
                    names = ", ".join(Path(p).name for p in group["input_paths"])
                    log(f"Step 06: [{names}]", "success")
                except Exception as exc:
                    record["error"] = str(exc)
                    block(group, f"Tạo enhance chưa xác định kết quả: {exc}")
                checkpoint()
        attempt["status"] = "submitted"
        checkpoint()
        return attempt

    def collect(attempts):
        pending = [(a, listing, e) for a in attempts for listing in a["listings"]
                   for e in listing["enhances"] if e.get("status") == "pending"]
        deadline = time.monotonic() + poll_timeout
        poll_number = 0
        total_pending = len(pending)
        if pending:
            log("Step 07: Kiểm tra trạng thái ảnh...", "info")
        while pending and not cancelled() and time.monotonic() < deadline:
            poll_number += 1
            ready_count = 0
            for attempt, listing, record in list(pending):
                if cancelled():
                    break
                enhance_id = record["enhance_id"]
                group = groups[record["output_id"]]
                try:
                    if not check_ready(enhance_id):
                        continue
                    raw_dir = (Path(manifest["output_dir"]) / "raw" /
                               f'{manifest["prefix"]}{attempt["number"]:02d}')
                    raw_dir.mkdir(parents=True, exist_ok=True)
                    raw_name = Path(group["output_name"]).stem + ".jpg"
                    log(f"Step 08: Đang tải biến thể - {raw_name} ({group.get('rendition') or 'edited'})", "info")
                    downloaded = download(enhance_id, raw_dir, raw_name, group.get("rendition"))
                    if not downloaded:
                        continue
                    group["rendition"] = downloaded["rendition"]
                    variant = {"enhance_id": enhance_id, "listing_id": listing["listing_id"],
                               "attempt": attempt["number"], "path": str(downloaded["path"]),
                               "rendition": downloaded["rendition"]}
                    group["variants"].append(variant)
                    record.update(status="downloaded", path=variant["path"], rendition=variant["rendition"])
                    pending.remove((attempt, listing, record))
                    ready_count += 1
                except Exception as exc:
                    # Retry poll/download against the SAME enhance until timeout.
                    record["error"] = str(exc)
                    log(f'{group["output_name"]}: chờ tải lại enhance {enhance_id}: {exc}', "warn")
                    continue
                checkpoint()
                if len(group["variants"]) >= 2 and group["status"] == "need_variant" and not cancelled():
                    try:
                        log(f'Step 09: So sánh watermark - {group["output_name"]}', "info")
                        result = clean(group["output_id"], group["output_name"],
                                       [v["path"] for v in group["variants"]],
                                       manifest["output_dir"], is_cancelled=cancelled)
                        group.update({key: result[key] for key in
                                      ("status", "reason", "output_path", "report_path", "preview_path")
                                      if key in result})
                        msg, level = format_cleaner_result_vn(group["output_name"], result, with_step=True)
                        log(msg, level)
                    except Exception as exc:
                        block(group, f"Không thể ghép ảnh: {exc}")
                    checkpoint()
            if pending and not cancelled():
                delay = min(poll_interval(poll_number, ready_count), max(0, deadline - time.monotonic()))
                log(f"Step 07: Kiểm tra lần {poll_number} - ready={ready_count}/{total_pending}, pending={len(pending)}, chờ {int(delay)}s.", "info")
                sleep(delay)
            elif ready_count:
                log(f"Step 07: Trạng thái kiểm tra - ready={total_pending - len(pending)}/{total_pending}.", "success")
        if not cancelled():
            for attempt, listing, record in pending:
                record["status"] = "download_pending"
                eid = str(record.get("enhance_id") or "")[:8]
                block(groups[record["output_id"]],
                      f'Chưa tải được enhance {eid}; vui lòng tải manual để tiếp tục.')
        for attempt in attempts:
            attempt["status"] = "stopped" if cancelled() else "collected"
        checkpoint()

    checkpoint()
    try:
        first = submit(1, list(groups.values())) if not cancelled() else None
        # Both initial attempts run remotely before we start waiting for results.
        second = submit(2, [g for g in groups.values() if g["status"] == "need_variant"]) if not cancelled() else None
        collect([a for a in (first, second) if a is not None])
        number = 3
        while not cancelled():
            selected = [g for g in groups.values() if g["status"] == "need_variant"]
            if not selected:
                break
            log(f"Step 11: Tự động chạy lượt {number:02d} cho {len(selected)} ảnh chưa sạch watermark...", "info")
            attempt_record = submit(number, selected)
            collect([attempt_record])
            number += 1
    finally:
        if cancelled():
            manifest["status"] = "stopped"
        checkpoint()
        shutil.rmtree(Path(manifest["output_dir"]) / "attempts", ignore_errors=True)
        shutil.rmtree(Path(manifest["output_dir"]) / "reports", ignore_errors=True)
    summary_result = summary(manifest)
    if summary_result.get("status") == "success":
        log(f"Hoàn tất: Đã làm sạch watermark toàn bộ {summary_result['cleaned_count']}/{summary_result['target_count']} ảnh.", "success")
    elif summary_result.get("status") == "partial":
        log(f"Hoàn tất một phần: Đã làm sạch {summary_result['cleaned_count']}/{summary_result['target_count']} ảnh (còn chờ {summary_result['target_count'] - summary_result['cleaned_count']} ảnh).", "warn")
    return summary_result
