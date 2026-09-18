"""Autoenhance End-to-End Workflow & Step-Aware Job Executor.

Orchestrates the complete Autoenhance pipeline:
  Auth -> Prepare -> CreateOrder -> Upload -> Execute -> Polling -> Download -> Export
with step-aware manual restarts, batch job planning, and shared jobs runner integration.
"""
from __future__ import annotations

import concurrent.futures
import json
import time
from pathlib import Path
from PIL import Image
import requests

from core.autoenhance.auth import load_api_key
from core.autoenhance.client import _api_get
from core.autoenhance.config import load_autoenhance_config
from core.autoenhance.constants import (
    API_BASE,
    DEFAULT_PROCESS_OPTIONS,
    JOB_STEPS,
    MAX_API_WORKERS,
    NATIVE_EXTS,
)
from core.autoenhance.download import download_and_process_image
from core.autoenhance.execute import map_options_to_payload, trigger_process
from core.autoenhance.metadata import load_order_metadata, save_order_metadata
from core.autoenhance.orders import create_order, filter_final_processed_images
from core.autoenhance.polling import poll_order_completion
from core.autoenhance.upload import get_upload_s3_info, prepare_upload_files, _upload_one_file
from core.shared.events import StepEvent, StepTracker
from core.shared.jobs.models import (
    BatchResult,
    JobResult,
    JobSpec,
    JobValidationError,
    OutputSpec,
)
from core.shared.jobs.planner import plan_jobs
from core.shared.jobs.runner import JobContext, restart_job, run_jobs
from core.shared.jobs.store import JobStore
from core.shared.workspace import TemporaryWorkspace


def build_autoenhance_outputs(input_dir: str | Path | Sequence[str | Path]) -> list[OutputSpec]:
    """Thu thập và ánh xạ danh sách ảnh đầu vào thành các OutputSpec độc lập.
    
    Quy định: Một ảnh đầu vào hợp lệ tương ứng một output logic.
    Đảm bảo output_id luôn là duy nhất (chống trùng lặp giữa a.jpg, a.png, a_2.jpg).
    """
    source_files: list[Path] = []
    if isinstance(input_dir, (str, Path)):
        p_in = Path(input_dir).resolve()
        if p_in.is_file():
            source_files = [p_in]
        elif p_in.is_dir():
            for f in sorted(p_in.iterdir()):
                if f.is_file() and not f.name.startswith("."):
                    ext = f.suffix.lower()
                    if ext in NATIVE_EXTS or ext in (".raw", ".dng", ".cr2", ".nef", ".arw"):
                        source_files.append(f)
    else:
        for item in input_dir:
            p_item = Path(item).resolve()
            if p_item.is_file() and not p_item.name.startswith("."):
                source_files.append(p_item)

    if not source_files:
        raise JobValidationError(f"Không tìm thấy ảnh hợp lệ trong thư mục đầu vào: {input_dir}")

    outputs: list[OutputSpec] = []
    used_ids: set[str] = set()
    for f in source_files:
        base_id = f.stem
        out_id = base_id
        counter = 1
        while out_id in used_ids:
            counter += 1
            out_id = f"{base_id}_{counter}"
        used_ids.add(out_id)

        outputs.append(
            OutputSpec(
                output_id=out_id,
                input_files=[f],
                metadata={"original_name": f.name, "extension": f.suffix.lower()},
            )
        )

    return outputs


def _is_enhanced_completed(img: dict[str, Any]) -> bool:
    """Kiểm tra ảnh đã hoàn tất xử lý AI trên Autoenhance."""
    status = str(img.get("status", "")).lower()
    return status in ("processed", "completed", "done", "success", "enhanced") or img.get("enhanced") is True


def _write_job_manifest(
    *,
    out_dir: Path,
    context: JobContext,
    spec: JobSpec,
    order_id: str | None,
    status: str,
    step: str,
    succeeded_outputs: Sequence[str],
    failed_outputs: Sequence[dict[str, Any]],
    downloaded_records: Sequence[dict[str, Any]],
    error_message: str | None = None,
    cancel_reason: str | None = None,
) -> Path:
    """Ghi file manifest lưu vết kiểm toán cho attempt hiện tại của Job."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / f"manifest_{context.run_id}_{spec.job_id}_att{context.attempt_no}.json"
    manifest_data = {
        "run_id": context.run_id,
        "job_id": spec.job_id,
        "attempt_no": context.attempt_no,
        "engine": spec.engine,
        "order_id": order_id or "",
        "status": status,
        "step": step,
        "error_message": error_message,
        "cancel_reason": cancel_reason,
        "outputs_total": len(spec.outputs),
        "outputs_succeeded": len(succeeded_outputs),
        "outputs_failed": len(failed_outputs),
        "items": list(downloaded_records),
        "finished_at": time.time(),
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)
    return manifest_path


def autoenhance_executor(spec: JobSpec, context: JobContext) -> JobResult:
    """Thực thi một Job của Autoenhance theo máy trạng thái step-by-step.
    
    Hỗ trợ restart thông minh theo 3 nhóm step:
    - Trước polling (prepare, create_order, upload, execute): Chạy lại từ đầu với input của job đó, tạo order mới.
    - Tại polling: Tiếp tục polling bằng order_id đã lưu trong context.server_resources.
    - Sau polling (download, export): Tiếp tục tải các output chưa hoàn tất, không tải đè file đã tải xong.
    """
    job_start = time.time()
    out_dir = Path(spec.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    failed_outputs: list[dict[str, Any]] = []
    succeeded_outputs: list[str] = []
    downloaded_records: list[dict[str, Any]] = []

    # Kiểm tra điểm tiếp tục khi restart
    prev_step = (
        context.resume_step
        if context.resume_step
        else (context.previous_attempt.step if context.previous_attempt else context.latest_step)
    )
    saved_order_id = str(context.server_resources.get("order_id", ""))
    order_id = saved_order_id

    def _create_result(
        status: str,
        step: str,
        *,
        error_message: str | None = None,
        cancel_reason: str | None = None,
        extra_server_resources: dict[str, Any] | None = None,
    ) -> JobResult:
        res_server_res = dict(extra_server_resources or {})
        if order_id:
            res_server_res.setdefault("order_id", order_id)
        _write_job_manifest(
            out_dir=out_dir,
            context=context,
            spec=spec,
            order_id=order_id,
            status=status,
            step=step,
            succeeded_outputs=succeeded_outputs,
            failed_outputs=failed_outputs,
            downloaded_records=downloaded_records,
            error_message=error_message,
            cancel_reason=cancel_reason,
        )
        return JobResult(
            job_id=spec.job_id,
            status=status,
            attempt_no=context.attempt_no,
            step=step,
            outputs_succeeded=list(succeeded_outputs),
            outputs_failed=list(failed_outputs),
            server_resources=res_server_res,
            error_message=error_message,
            cancel_reason=cancel_reason,
            started_at=job_start,
            finished_at=time.time(),
        )

    tracker = StepTracker(
        engine="Autoenhance",
        steps=JOB_STEPS,
        event_fn=context.event_fn,
        log_fn=context.log_fn,
        run_id=context.run_id,
        job_id=spec.job_id,
    )

    # 1. Auth (Kiểm tra API key đăng nhập dịch vụ Autoenhance)
    tracker.start_step("auth", "Đang kiểm tra thông tin xác thực Autoenhance...")
    if context.attempt_no == 1:
        context.record_checkpoint("auth", "running")

    actual_key = (
        context.credentials.get("api_key")
        or spec.preferences.get("api_key")
        or load_api_key()
        or ""
    ).strip()

    if not actual_key:
        tracker.complete_step("auth", "failed", "Vui lòng đăng nhập Autoenhance trước.")
        context.record_checkpoint("auth", "failed", error_message="Chưa có API key Autoenhance")
        return _create_result("failed", "auth", error_message="Vui lòng đăng nhập Autoenhance trước.")
    tracker.complete_step("auth", "success", "Xác thực API key đầu vào hợp lệ.")

    is_restart_at_polling = (
        context.attempt_no > 1
        and prev_step in ("polling",)
    )
    is_restart_at_download = (
        context.attempt_no > 1
        and prev_step in ("download", "export")
    )

    if (is_restart_at_polling or is_restart_at_download) and not saved_order_id:
        err_checkpoint = "Lỗi checkpoint: thiếu order_id để tiếp tục tác vụ."
        tracker.start_step(prev_step, "Đang khôi phục phiên xử lý từ checkpoint...")
        tracker.complete_step(prev_step, "failed", err_checkpoint)
        context.record_checkpoint(prev_step, "failed", error_message=err_checkpoint)
        return _create_result("failed", prev_step, error_message=err_checkpoint)

    with requests.Session() as sess:
        if not is_restart_at_polling and not is_restart_at_download:
            # Thu thập các input files của job
            job_input_files: list[Path] = [
                f for out in spec.outputs for f in out.input_files
            ]

            with TemporaryWorkspace(prefix="autoenhance_job_conv_") as temp_conv_dir:
                # 2. Prepare: Chuẩn bị & Chuyển đổi định dạng
                tracker.start_step("prepare", "Đang thu thập và chuẩn bị danh sách ảnh...")
                context.record_checkpoint("prepare", "running")
                try:
                    ready_files, meta_dims = prepare_upload_files(
                        input_dir=job_input_files,
                        temp_conv_dir=temp_conv_dir,
                        log_fn=context.log,
                        stop_event=context.stop_event,
                    )
                except Exception as exc:
                    err_msg = f"Lỗi chuẩn bị ảnh: {exc}"
                    tracker.complete_step("prepare", "failed", err_msg)
                    context.record_checkpoint("prepare", "failed", error_message=err_msg)
                    return _create_result("failed", "prepare", error_message=err_msg)

                if context.is_cancelled():
                    tracker.complete_step("prepare", "cancelled", "Tác vụ bị dừng trong khi chuẩn bị ảnh.")
                    context.record_checkpoint("prepare", "cancelled", cancel_reason="stopped_during_execution")
                    return _create_result("cancelled", "prepare", cancel_reason="stopped_during_execution")

                if not ready_files or len(ready_files) != len(job_input_files):
                    err_msg = f"Không đủ ảnh sẵn sàng ({len(ready_files)}/{len(job_input_files)})."
                    tracker.complete_step("prepare", "failed", err_msg)
                    context.record_checkpoint("prepare", "failed", error_message=err_msg)
                    return _create_result("failed", "prepare", error_message=err_msg)

                tracker.complete_step("prepare", "success", f"Đã chuẩn bị xong {len(ready_files)} ảnh hợp lệ.")
                context.record_checkpoint("prepare", "success")

                # 3. Create Order
                custom_order_name = spec.preferences.get("order_name")
                order_name_val = custom_order_name or f"Order_{context.run_id}_{spec.job_id}"
                tracker.start_step("create_order", f"Đang tạo đơn hàng mới trên Autoenhance... ({order_name_val})")
                context.record_checkpoint("create_order", "running")
                try:
                    order_res = create_order(api_key=actual_key, order_name=order_name_val, log_fn=context.log, session=sess)
                    order_id = str(order_res.get("order_id") or order_res.get("id", ""))
                    if not order_id:
                        raise ValueError("Không nhận được order_id từ API Autoenhance.")
                    context.record_checkpoint("create_order", "success", server_resources={"order_id": order_id})
                    tracker.complete_step("create_order", "success", f"Đã tạo order thành công: {order_id}")
                except Exception as exc:
                    tracker.complete_step("create_order", "failed", f"Lỗi tạo order: {exc}")
                    context.record_checkpoint("create_order", "failed", error_message=str(exc))
                    return _create_result("failed", "create_order", error_message=str(exc))

                try:
                    save_order_metadata(order_id, meta_dims)
                except Exception as exc:
                    context.log(f"Cảnh báo: Không thể lưu metadata kích thước: {exc}", "warn")

                if context.is_cancelled():
                    tracker.complete_step("create_order", "cancelled", "Tác vụ bị dừng.")
                    context.record_checkpoint("create_order", "cancelled", cancel_reason="stopped_during_execution")
                    return _create_result("cancelled", "create_order", cancel_reason="stopped_during_execution")

                # 4. Upload: Tải ảnh lên AWS S3
                total_expected = len(ready_files)
                tracker.start_step("upload", f"Đang tải lên {total_expected} ảnh...", current=0, total=total_expected)
                context.record_checkpoint("upload", "running", server_resources={"order_id": order_id})

                upload_jobs: list[tuple[Path, dict[str, Any]]] = []
                for f in ready_files:
                    if context.is_cancelled():
                        break
                    try:
                        up_info = get_upload_s3_info(actual_key, order_id, f.name, log_fn=context.log, session=sess)
                        upload_jobs.append((f, up_info))
                    except Exception as exc:
                        failed_outputs.append({"output_id": f.stem, "error": f"Lấy link upload thất bại: {exc}"})

                if context.is_cancelled():
                    tracker.complete_step("upload", "cancelled", "Upload bị dừng bởi người dùng.")
                    context.record_checkpoint("upload", "cancelled", cancel_reason="stopped_during_execution")
                    return _create_result("cancelled", "upload", cancel_reason="stopped_during_execution")

                if not upload_jobs:
                    tracker.complete_step("upload", "failed", "Không lấy được link upload cho bất kỳ ảnh nào.")
                    context.record_checkpoint("upload", "failed", server_resources={"order_id": order_id}, error_message="Upload link error")
                    return _create_result("failed", "upload", error_message="Không lấy được link upload cho bất kỳ ảnh nào.")

                uploaded_count = 0
                with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_API_WORKERS) as executor:
                    future_map = {
                        executor.submit(_upload_one_file, f_path, s3_info, context.stop_event): f_path
                        for f_path, s3_info in upload_jobs
                    }
                    for future in concurrent.futures.as_completed(future_map):
                        f_path = future_map[future]
                        try:
                            ok = future.result()
                            if ok:
                                uploaded_count += 1
                                tracker.progress_step(
                                    "upload",
                                    f"Đã tải lên {uploaded_count}/{total_expected} ảnh",
                                    current=uploaded_count,
                                    total=total_expected,
                                )
                            else:
                                failed_outputs.append({"output_id": f_path.stem, "error": "Upload S3 trả về thất bại"})
                        except Exception as exc:
                            failed_outputs.append({"output_id": f_path.stem, "error": f"Ngoại lệ upload: {exc}"})

                if uploaded_count == 0:
                    tracker.complete_step("upload", "failed", "Không có ảnh nào upload thành công lên S3.")
                    context.record_checkpoint("upload", "failed", server_resources={"order_id": order_id})
                    return _create_result("failed", "upload", error_message="Không có ảnh nào upload thành công lên S3.")
                elif uploaded_count == total_expected and len(failed_outputs) == 0:
                    tracker.complete_step("upload", "success", f"Đã tải lên đủ {uploaded_count}/{total_expected} ảnh.")
                    context.record_checkpoint("upload", "success", server_resources={"order_id": order_id})
                else:
                    tracker.complete_step("upload", "partial", f"Tải lên thành công {uploaded_count}/{total_expected} ảnh ({len(failed_outputs)} lỗi).")
                    context.record_checkpoint("upload", "partial", server_resources={"order_id": order_id})

                if context.is_cancelled():
                    tracker.complete_step("execute", "cancelled", "Tác vụ dừng trước khi kích hoạt xử lý AI.")
                    context.record_checkpoint("execute", "cancelled", cancel_reason="stopped_during_execution")
                    return _create_result("cancelled", "execute", cancel_reason="stopped_during_execution")

                # 5. Execute: Kích hoạt xử lý AI
                tracker.start_step("execute", "Đang gửi yêu cầu xử lý AI...")
                context.record_checkpoint("execute", "running", server_resources={"order_id": order_id})
                process_payload = map_options_to_payload(spec.preferences, log_fn=context.log)
                ok_process = trigger_process(actual_key, order_id, process_payload, log_fn=context.log, session=sess)
                if not ok_process:
                    tracker.complete_step("execute", "failed", "Không thể kích hoạt tiến trình xử lý trên Autoenhance.")
                    context.record_checkpoint("execute", "failed", server_resources={"order_id": order_id})
                    return _create_result("failed", "execute", error_message="Không thể kích hoạt xử lý trên Autoenhance.")

                tracker.complete_step("execute", "success", "Đã gửi yêu cầu xử lý AI thành công.")
                context.record_checkpoint("execute", "success", server_resources={"order_id": order_id})

        # 6. Polling: Chờ xử lý hoàn tất
        if not is_restart_at_download:
            tracker.start_step("polling", f"Đang theo dõi trạng thái xử lý order {order_id}...")
            context.record_checkpoint("polling", "running", server_resources={"order_id": order_id})
            ord_det, successful, failed_imgs = poll_order_completion(
                api_key=actual_key,
                order_id=order_id,
                max_wait_seconds=1800,
                poll_interval=5,
                log_fn=context.log,
                stop_event=context.stop_event,
                session=sess,
            )

            if context.is_cancelled():
                tracker.complete_step("polling", "cancelled", "Đã dừng theo dõi tiến trình xử lý.")
                context.record_checkpoint("polling", "cancelled", cancel_reason="stopped_during_execution")
                return _create_result("cancelled", "polling", cancel_reason="stopped_during_execution")

            for f_item in failed_imgs:
                failed_outputs.append({
                    "output_id": f_item.get("id") or f_item.get("filename", "unknown"),
                    "error": f_item.get("error", "AI enhancement failed on server"),
                })

            if ord_det is None or len(successful) == 0:
                tracker.complete_step("polling", "failed", "Toàn bộ ảnh đều thất bại hoặc quá thời gian chờ xử lý.")
                context.record_checkpoint("polling", "failed", server_resources={"order_id": order_id})
                return _create_result("failed", "polling", error_message="Toàn bộ ảnh đều thất bại hoặc quá thời gian chờ xử lý.")

            imgs = ord_det.get("images", [])
            if len(failed_imgs) > 0:
                tracker.complete_step("polling", "partial", f"Đã xử lý xong {len(successful)}/{len(imgs)} ảnh ({len(failed_imgs)} lỗi).")
                context.record_checkpoint("polling", "partial", server_resources={"order_id": order_id})
            else:
                tracker.complete_step("polling", "success", f"Đã xử lý thành công toàn bộ {len(successful)}/{len(imgs)} ảnh.")
                context.record_checkpoint("polling", "success", server_resources={"order_id": order_id})
        else:
            # Khi restart tại download, lấy danh sách ảnh đã xử lý từ API
            ord_res = _api_get(f"{API_BASE}/orders/{order_id}/", actual_key, log_fn=context.log, session=sess)
            ord_det = ord_res.json() if ord_res is not None and ord_res.status_code == 200 else {}
            all_imgs = ord_det.get("images", []) if isinstance(ord_det, dict) else []
            successful = [img for img in all_imgs if _is_enhanced_completed(img)]
            failed_imgs = [img for img in all_imgs if str(img.get("status", "")).lower() in ("failed", "error") or img.get("error") is True]
            for f_item in failed_imgs:
                failed_outputs.append({
                    "output_id": f_item.get("id") or f_item.get("filename", "unknown"),
                    "error": f_item.get("error", "AI enhancement error"),
                })

        # Lọc chỉ giữ lại các ảnh kết quả thành phẩm của bước cuối cùng (Step 6 / Final Output)
        final_images = filter_final_processed_images(
            order_id=order_id,
            images=successful,
            api_key=actual_key,
            session=sess,
            log_fn=context.log,
        )

        # 7. Download: Tải ảnh kết quả về thư mục đích
        dl_target_count = len(final_images)
        tracker.start_step("download", f"Bắt đầu tải {dl_target_count} ảnh hoàn tất về thư mục...", current=0, total=dl_target_count)
        context.record_checkpoint("download", "running", server_resources={"order_id": order_id})

        meta_dims = load_order_metadata(order_id)
        previously_downloaded = set(context.server_resources.get("downloaded_files", []))

        with TemporaryWorkspace(prefix=f"ae_dl_{context.run_id}_{spec.job_id}_") as ws_dir:
            for img in final_images:
                if context.is_cancelled():
                    break

                img_id = str(img.get("image_id") or img.get("id", ""))
                orig_name = str(img.get("image_name") or img.get("filename") or f"{img_id}.jpg")
                clean_stem = Path(orig_name).stem.strip() or img_id[:8]
                expected_filename = f"{clean_stem}.jpg"
                target_path = out_dir / expected_filename

                has_download_tracking = (
                    "downloaded_files" in context.server_resources
                    or (context.previous_attempt and "downloaded_files" in context.previous_attempt.server_resources)
                )
                if has_download_tracking:
                    is_recorded = (
                        img_id in previously_downloaded
                        or orig_name in previously_downloaded
                        or expected_filename in previously_downloaded
                    )
                else:
                    is_recorded = True

                can_reuse = False
                if is_restart_at_download and is_recorded:
                    if target_path.is_file() and target_path.stat().st_size > 0:
                        try:
                            with Image.open(target_path) as img_verify:
                                img_verify.verify()
                            can_reuse = True
                        except Exception:
                            can_reuse = False

                if can_reuse:
                    succeeded_outputs.append(target_path.name)
                    downloaded_records.append({
                        "image_id": img_id,
                        "filename": target_path.name,
                        "file_path": str(target_path),
                        "reused": True,
                    })
                    done_count = len(succeeded_outputs)
                    tracker.progress_step(
                        "download",
                        f"Đã xác nhận có sẵn {target_path.name} ({done_count}/{dl_target_count})",
                        current=done_count,
                        total=dl_target_count,
                    )
                    continue

                saved_dim = meta_dims.get(orig_name) or meta_dims.get(Path(orig_name).name)
                target_dim = tuple(saved_dim) if isinstance(saved_dim, (list, tuple)) and len(saved_dim) == 2 else None

                dl_ok, final_path, dl_err = download_and_process_image(
                    image_id=img_id,
                    original_filename=orig_name,
                    output_dir=out_dir,
                    api_key=actual_key,
                    temp_dir=ws_dir,
                    target_dim=target_dim,
                    stop_event=context.stop_event,
                    log_fn=context.log,
                )

                if dl_ok and final_path is not None:
                    succeeded_outputs.append(final_path.name)
                    downloaded_records.append({
                        "image_id": img_id,
                        "filename": final_path.name,
                        "file_path": str(final_path),
                        "reused": False,
                    })
                    current_downloaded = list(previously_downloaded)
                    if img_id not in current_downloaded:
                        current_downloaded.append(img_id)
                    if final_path.name not in current_downloaded:
                        current_downloaded.append(final_path.name)
                    context.record_checkpoint(
                        "download",
                        "running",
                        server_resources={"order_id": order_id, "downloaded_files": current_downloaded},
                    )
                    done_count = len(succeeded_outputs)
                    tracker.progress_step(
                        "download",
                        f"Đã tải thành công {final_path.name} ({done_count}/{dl_target_count})",
                        current=done_count,
                        total=dl_target_count,
                    )
                else:
                    failed_outputs.append({"output_id": orig_name, "error": dl_err or "Tải hoặc hậu xử lý ảnh thất bại"})

        if context.is_cancelled():
            tracker.complete_step("download", "cancelled", f"Tác vụ bị dừng. Đã tải {len(succeeded_outputs)}/{dl_target_count} ảnh.")
            context.record_checkpoint("download", "cancelled", cancel_reason="stopped_during_execution")
            return _create_result("cancelled", "download", cancel_reason="stopped_during_execution")

        if len(succeeded_outputs) >= dl_target_count and len(failed_outputs) == 0:
            tracker.complete_step("download", "success", f"Đã tải đủ toàn bộ {len(succeeded_outputs)}/{dl_target_count} ảnh hoàn tất.")
            context.record_checkpoint("download", "success", server_resources={"order_id": order_id})
        elif len(succeeded_outputs) > 0:
            tracker.complete_step("download", "partial", f"Tải hoàn tất {len(succeeded_outputs)}/{dl_target_count} ảnh.")
            context.record_checkpoint("download", "partial", server_resources={"order_id": order_id})
        else:
            tracker.complete_step("download", "failed", "Không tải được ảnh kết quả nào về thư mục đích.")
            context.record_checkpoint("download", "failed", server_resources={"order_id": order_id})

    # 8. Export: Xuất file và manifest theo attempt
    tracker.start_step("export", "Đang hoàn tất tác vụ...")
    expected_outputs = dl_target_count if dl_target_count > 0 else len(spec.outputs)
    if len(succeeded_outputs) >= expected_outputs and len(failed_outputs) == 0:
        job_status = "success"
        tracker.complete_step("export", "success", f"Hoàn tất 100% ({len(succeeded_outputs)}/{expected_outputs} outputs).")
    elif len(succeeded_outputs) > 0:
        job_status = "partial"
        tracker.complete_step("export", "partial", f"Hoàn thành một phần: {len(succeeded_outputs)}/{expected_outputs} outputs.")
    else:
        job_status = "failed"
        tracker.complete_step("export", "failed", "Không hoàn tất output nào thành công.")

    context.record_checkpoint("export", job_status, server_resources={"order_id": order_id})
    return _create_result(job_status, "export")


def run_workflow(
    *,
    input_dir: str | Path | Sequence[str | Path],
    output_dir: str | Path,
    mode: str = "single",
    api_key: str | None = None,
    options: dict[str, Any] | None = None,
    order_name: str | None = None,
    stop_event: Any | None = None,
    event_fn: Callable[[StepEvent], None] | None = None,
    log_fn: Callable[[str, str], None] | None = None,
    store: JobStore | None = None,
    run_id: str | None = None,
) -> BatchResult:
    """Entrypoint chính chạy quy trình Autoenhance tuần tự (single hoặc batch).
    
    Quy tắc:
    - Một ảnh đầu vào hợp lệ tương ứng một output logic.
    - Đọc capacity limits từ cấu hình engine (mặc định 20 output/job, 3 job/batch).
    - Lite chỉ chạy single; bị từ chối khi chọn batch. Plus chạy được cả single và batch.
    - Quản lý qua JobPlan, JobRunner và JobStore chuẩn hóa.
    - Credentials (API key) được truyền qua ngữ cảnh runtime, không lưu vào checkpoint.
    """
    cfg = load_autoenhance_config()
    limits = cfg.jobs.to_limits()

    outputs = build_autoenhance_outputs(input_dir)

    prefs = dict(DEFAULT_PROCESS_OPTIONS)
    if options:
        prefs.update(options)
    if order_name:
        prefs["order_name"] = order_name

    actual_key = (api_key or "").strip() or load_api_key() or ""

    plan = plan_jobs(
        engine="autoenhance",
        outputs=outputs,
        mode=mode,
        limits=limits,
        preferences=prefs,
        output_dir=output_dir,
        run_id=run_id,
    )

    return run_jobs(
        plan=plan,
        execute_job=autoenhance_executor,
        credentials={"api_key": actual_key} if actual_key else None,
        stop_event=stop_event,
        event_fn=event_fn,
        log_fn=log_fn,
        store=store,
    )


def restart_workflow_job(
    *,
    run_id: str,
    job_id: str,
    api_key: str | None = None,
    stop_event: Any | None = None,
    event_fn: Callable[[StepEvent], None] | None = None,
    log_fn: Callable[[str, str], None] | None = None,
    store: JobStore | None = None,
) -> JobResult:
    """Entrypoint chạy lại (manual restart) một job cụ thể trong phiên chạy Autoenhance."""
    actual_key = (api_key or "").strip() or load_api_key() or ""
    return restart_job(
        run_id=run_id,
        job_id=job_id,
        execute_job=autoenhance_executor,
        credentials={"api_key": actual_key} if actual_key else None,
        stop_event=stop_event,
        event_fn=event_fn,
        log_fn=log_fn,
        store=store,
    )
