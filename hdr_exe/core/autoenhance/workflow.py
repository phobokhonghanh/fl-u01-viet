"""Autoenhance End-to-End Workflow Pipeline with Step Tracking.

Coordinates the 7-step execution lifecycle:
1. Auth: Verify input api_key presence.
2. Prepare: Convert unsupported formats inside TemporaryWorkspace.
3. CreateOrder: Create remote order and record dimensions in metadata.
4. Upload: Fetch presigned AWS S3 URLs and stream files.
5. Execute: Check cancellation before sending AI process request.
6. Polling: Track remote enhancement status.
7. Download: Batch download with atomic protection and step continuity.
"""
from __future__ import annotations

import concurrent.futures
import time
from pathlib import Path
from typing import Any, Callable, Sequence

import requests

from core.autoenhance.constants import MAX_API_WORKERS, WORKFLOW_STEPS
from core.autoenhance.auth import load_api_key
from core.autoenhance.orders import create_order
from core.autoenhance.upload import prepare_upload_files, get_upload_s3_info, _upload_one_file
from core.autoenhance.execute import map_options_to_payload, trigger_process
from core.autoenhance.polling import poll_order_completion
from core.autoenhance.download import batch_download
from core.autoenhance.metadata import save_order_metadata
from core.shared.callbacks import ProgressAdapter
from core.shared.events import StepEvent, StepTracker
from core.shared.workspace import TemporaryWorkspace


def upload_and_process(
    input_dir: str | Path | Sequence[str | Path],
    savedir: str | Path,
    api_key: str | None = None,
    order_name: str | None = None,
    options: dict[str, Any] | None = None,
    log_fn: Callable[[str, str], None] | None = None,
    progress_fn: Callable[..., Any] | None = None,
    stop_event: Any = None,
    event_fn: Callable[[StepEvent], None] | None = None,
    run_id: str | None = None,
) -> bool:
    """Quy trình toàn diện Autoenhance 7 bước chuẩn hóa với StepTracker."""
    tr = StepTracker(
        engine="Autoenhance",
        steps=WORKFLOW_STEPS,
        run_id=run_id,
        event_fn=event_fn,
        log_fn=log_fn,
    )

    try:
        # -------------------------------------------------------------
        # Bước 1: Auth (1/7) - Kiểm tra api_key đầu vào không gọi mạng dư thừa
        # -------------------------------------------------------------
        tr.start_step("auth", "Đang kiểm tra thông tin xác thực...")
        actual_key = api_key or load_api_key() or ""

        if not actual_key or not actual_key.strip():
            tr.complete_step("auth", "failed", "Vui lòng đăng nhập Autoenhance trước.")
            return False

        tr.complete_step("auth", "success", "Xác thực API key đầu vào hợp lệ.")

        if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
            return False

        opts = options or {}
        out_p = Path(savedir) if savedir else Path.cwd()
        out_p.mkdir(parents=True, exist_ok=True)

        prog_adapter = ProgressAdapter(progress_fn, warning_fn=log_fn)

        # Quản lý thư mục chuyển đổi tạm bằng TemporaryWorkspace (tự dọn dẹp trong finally)
        with TemporaryWorkspace(prefix="autoenhance_conv_") as temp_conv_dir:
            # -------------------------------------------------------------
            # Bước 2: Prepare (2/7) - Thu thập và chuyển đổi định dạng ảnh
            # -------------------------------------------------------------
            tr.start_step("prepare", "Đang thu thập và chuẩn bị danh sách ảnh...")
            ready_files, meta_dims = prepare_upload_files(
                input_dir=input_dir,
                temp_conv_dir=temp_conv_dir,
                log_fn=log_fn,
                stop_event=stop_event,
            )

            if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
                tr.complete_step("prepare", "cancelled", "Tác vụ bị dừng trong khi chuẩn bị ảnh.")
                return False

            if not ready_files:
                tr.complete_step("prepare", "failed", "Không có ảnh nào sẵn sàng để upload.")
                return False

            tr.complete_step("prepare", "success", f"Đã chuẩn bị xong {len(ready_files)} ảnh hợp lệ.")

            if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
                return False

            # Quản lý vòng đời HTTP Session qua context manager cho các bước gọi API server
            with requests.Session() as sess:
                # -------------------------------------------------------------
                # Bước 3: Create Order (3/7) - Tạo đơn hàng và lưu metadata
                # -------------------------------------------------------------
                name = order_name or f"Order_{int(time.time())}"
                tr.start_step("create_order", f"Đang tạo đơn hàng mới trên Autoenhance... ({name})")
                try:
                    order_res = create_order(api_key=actual_key, order_name=name, log_fn=log_fn, session=sess)
                    order_id = order_res.get("order_id") or order_res.get("id")
                    if not order_id:
                        raise ValueError("Không nhận được order_id từ API Autoenhance.")
                    tr.complete_step("create_order", "success", f"Đã tạo order thành công: {order_id}")
                except Exception as e:
                    tr.complete_step("create_order", "failed", f"Lỗi tạo order: {e}")
                    return False

                # Lưu metadata kích thước gốc vào cấu hình chuẩn
                try:
                    save_order_metadata(order_id, meta_dims)
                except Exception as e:
                    if log_fn:
                        log_fn(f"Cảnh báo: Không thể lưu metadata kích thước: {e}", "warn")

                if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
                    return False

                # -------------------------------------------------------------
                # Bước 4: Upload (4/7) - Xin presigned URLs và upload ảnh lên S3
                # -------------------------------------------------------------
                tr.start_step("upload", f"Đang tải lên {len(ready_files)} ảnh...", current=0, total=len(ready_files))

                upload_jobs: list[tuple[Path, dict[str, Any]]] = []
                for f in ready_files:
                    if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
                        tr.complete_step("upload", "cancelled", "Tác vụ upload bị hủy.")
                        return False
                    try:
                        up_info = get_upload_s3_info(actual_key, order_id, f.name, log_fn=log_fn, session=sess)
                        upload_jobs.append((f, up_info))
                    except Exception as e:
                        if log_fn:
                            log_fn(f"Lỗi lấy link upload cho {f.name}: {e}", "warn")

                if not upload_jobs:
                    tr.complete_step("upload", "failed", "Không lấy được đường dẫn upload cho bất kỳ ảnh nào.")
                    return False

                uploaded_count = 0
                total_uploads = len(upload_jobs)

                with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_API_WORKERS) as executor:
                    future_map = {
                        executor.submit(_upload_one_file, f_path, s3_info, stop_event): f_path.name
                        for f_path, s3_info in upload_jobs
                    }
                    for future in concurrent.futures.as_completed(future_map):
                        fn_name = future_map[future]
                        try:
                            success = future.result()
                            if success:
                                uploaded_count += 1
                                prog_adapter(uploaded_count, total_uploads, fn_name)
                                tr.progress_step(
                                    "upload",
                                    f"Đã tải lên {uploaded_count}/{total_uploads} ảnh",
                                    current=uploaded_count,
                                    total=total_uploads,
                                )
                        except Exception as e:
                            if log_fn:
                                log_fn(f"  Lỗi tải lên {fn_name}: {e}", "error")

                if uploaded_count == 0:
                    tr.complete_step("upload", "failed", "Không có ảnh nào được upload thành công lên S3.", current=0, total=total_uploads)
                    return False
                elif uploaded_count == total_uploads:
                    tr.complete_step("upload", "success", f"Đã tải lên {uploaded_count}/{total_uploads} ảnh thành công.", current=uploaded_count, total=total_uploads)
                else:
                    tr.complete_step("upload", "partial", f"Tải lên thành công {uploaded_count}/{total_uploads} ảnh.", current=uploaded_count, total=total_uploads)

                # -------------------------------------------------------------
                # Kiểm tra dừng trước khi gửi lệnh xử lý (QUAN TRỌNG: Ngăn gửi process khi đã hủy)
                # -------------------------------------------------------------
                if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
                    tr.complete_step("execute", "cancelled", "Tác vụ dừng cục bộ trước khi kích hoạt xử lý trên server.")
                    return False

                # -------------------------------------------------------------
                # Bước 5: Execute (5/7) - Kích hoạt tiến trình xử lý AI
                # -------------------------------------------------------------
                tr.start_step("execute", "Đang gửi yêu cầu xử lý AI...")
                process_payload = map_options_to_payload(opts, log_fn=log_fn)
                ok_process = trigger_process(actual_key, order_id, process_payload, log_fn=log_fn, session=sess)
                if not ok_process:
                    tr.complete_step("execute", "failed", "Không thể kích hoạt tiến trình xử lý trên Autoenhance.")
                    return False

                tr.complete_step("execute", "success", "Đã gửi yêu cầu xử lý AI thành công.")

                # -------------------------------------------------------------
                # Bước 6: Polling (6/7) - Theo dõi trạng thái hoàn tất
                # -------------------------------------------------------------
                tr.start_step("polling", "Đang theo dõi trạng thái xử lý trên server...")
                ord_det, successful, failed = poll_order_completion(
                    api_key=actual_key,
                    order_id=order_id,
                    max_wait_seconds=1800,
                    poll_interval=5,
                    log_fn=log_fn,
                    stop_event=stop_event,
                    session=sess,
                )

                if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
                    tr.complete_step("polling", "cancelled", "Đã dừng theo dõi tiến trình xử lý.")
                    return False

                if ord_det is None or len(successful) == 0:
                    tr.complete_step("polling", "failed", "Toàn bộ ảnh đều thất bại hoặc quá thời gian chờ xử lý.")
                    return False

                imgs = ord_det.get("images", [])
                if len(failed) > 0:
                    tr.complete_step("polling", "partial", f"Đã xử lý xong {len(successful)}/{len(imgs)} ảnh ({len(failed)} ảnh lỗi).")
                else:
                    tr.complete_step("polling", "success", f"Đã xử lý thành công toàn bộ {len(successful)}/{len(imgs)} ảnh.")

        # -------------------------------------------------------------
        # Bước 7: Download (7/7) - Tải ảnh kết quả về thư mục đích
        # Mẫu số tiến độ nhất quán: total = len(successful)
        # -------------------------------------------------------------
        dl_target_count = len(successful)
        tr.start_step("download", f"Bắt đầu tải {dl_target_count} ảnh hoàn tất về thư mục...", current=0, total=dl_target_count)

        dl_count = batch_download(
            api_key=actual_key,
            order_ids=[order_id],
            savedir=out_p,
            log_fn=log_fn,
            progress_fn=progress_fn,
            stop_event=stop_event,
            tracker=tr,
        )

        if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
            tr.complete_step("download", "cancelled", f"Tác vụ bị dừng. Đã tải {dl_count}/{dl_target_count} ảnh.", current=dl_count, total=dl_target_count)
            return False

        if dl_count == dl_target_count and len(failed) == 0:
            tr.complete_step("download", "success", f"Đã tải đủ toàn bộ {dl_count}/{dl_target_count} ảnh hoàn tất.", current=dl_count, total=dl_target_count)
            return True
        elif dl_count > 0:
            tr.complete_step("download", "partial", f"Tải hoàn tất {dl_count}/{dl_target_count} ảnh ({len(failed)} ảnh lỗi trên server).", current=dl_count, total=dl_target_count)
            return True
        else:
            tr.complete_step("download", "failed", "Không tải được ảnh kết quả nào về thư mục đích.", current=0, total=dl_target_count)
            return False

    except Exception as exc:
        if log_fn:
            log_fn(f"Ngoại lệ chưa bắt trong workflow: {exc}", "error")
        tr.fail_active_step(f"Ngoại lệ hệ thống: {exc}")
        return False
