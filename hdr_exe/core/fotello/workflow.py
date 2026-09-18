"""Fotello End-to-End Workflow & Step-Aware Job Executor.

Orchestrates the complete Fotello pipeline:
  Auth -> Prepare -> Upload -> Create Listing -> Execute -> Polling -> Download -> Export
with step-aware manual restart, collision-safe downloads, and shared jobs integration.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import time
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from core.fotello import auth, client, listings
from core.fotello.brackets import BracketValidationError, build_bracket_outputs, verify_inputs_integrity
from core.fotello.config import load_fotello_config
from core.fotello.execute import create_job_resources
from core.fotello.polling import poll_enhances_completion
from core.fotello.upload import upload_job_inputs
from core.shared.events import StepEvent, StepTracker
from core.shared.jobs.models import (
    BatchResult,
    JobResult,
    JobSpec,
)
from core.shared.jobs.planner import plan_jobs
from core.shared.jobs.runner import JobContext, restart_job, run_jobs
from core.shared.jobs.store import JobStore

from core.fotello.constants import JOB_STEPS


def fotello_executor(spec: JobSpec, context: JobContext) -> JobResult:
    """Thực thi một Job của Fotello theo máy trạng thái step-by-step.

    Hỗ trợ restart thông minh theo 3 nhóm step:
    - Trước polling (prepare, upload, create_listing, execute): Chạy lại từ đầu với toàn bộ input, tạo listing mới.
    - Tại polling: Tiếp tục polling bằng listing_id / enhance_ids đã lưu.
    - Sau polling (download, export): Tiếp tục tải các output chưa hoàn tất.
    """
def _write_job_manifest(
    *,
    out_dir: Path,
    context: JobContext,
    spec: JobSpec,
    listing_id: str | None,
    status: str,
    step: str,
    succeeded_outputs: Sequence[str],
    failed_outputs: Sequence[dict[str, Any]],
    downloaded_records: Sequence[dict[str, Any]],
    error_message: str | None = None,
    cancel_reason: str | None = None,
) -> Path:
    """Ghi file manifest lưu vết kiểm toán cho attempt hiện tại của Job Fotello."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / f"manifest_{context.run_id}_{spec.job_id}_att{context.attempt_no}.json"
    manifest_data = {
        "run_id": context.run_id,
        "job_id": spec.job_id,
        "attempt_no": context.attempt_no,
        "engine": spec.engine,
        "listing_id": listing_id or "",
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


def fotello_executor(spec: JobSpec, context: JobContext) -> JobResult:
    """Thực thi một Job của Fotello theo máy trạng thái step-by-step.
    
    Hỗ trợ restart thông minh:
    - Trước polling (prepare, upload, create_listing, execute): Chạy lại từ đầu với toàn bộ input, tạo listing mới.
    - Tại polling: Tiếp tục polling bằng listing_id / enhance_ids đã lưu.
    - Sau polling (download, export): Tiếp tục tải các output chưa hoàn tất.
    """
    cfg = load_fotello_config()
    job_start = time.time()
    out_dir = Path(spec.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    failed_outputs: list[dict[str, Any]] = []
    succeeded_outputs: list[str] = []
    downloaded_records: list[dict[str, Any]] = []

    # Kiểm tra điểm bắt đầu khi restart
    prev_step = (
        context.resume_step
        if context.resume_step
        else (context.previous_attempt.step if context.previous_attempt else context.latest_step)
    )
    listing_id = str(context.server_resources.get("listing_id", ""))
    output_enhance_map: dict[str, str] = dict(context.server_resources.get("enhance_map", {}))

    def _create_result(
        status: str,
        step: str,
        *,
        error_message: str | None = None,
        cancel_reason: str | None = None,
        extra_server_resources: dict[str, Any] | None = None,
    ) -> JobResult:
        res_server_res = dict(extra_server_resources or {})
        if listing_id:
            res_server_res.setdefault("listing_id", listing_id)
        if output_enhance_map:
            res_server_res.setdefault("enhance_map", output_enhance_map)
        _write_job_manifest(
            out_dir=out_dir,
            context=context,
            spec=spec,
            listing_id=listing_id,
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
        engine="Fotello",
        steps=JOB_STEPS,
        run_id=context.run_id,
        job_id=spec.job_id,
        event_fn=context.event_fn,
        log_fn=context.log_fn,
    )

    # 1. Xác thực (Auth)
    tracker.start_step("auth", "Đang kiểm tra phiên làm việc...")
    if context.attempt_no == 1:
        context.record_checkpoint("auth", "running")

    state, msg, tokens = auth.check_auth_session()
    if state != "valid" or not tokens:
        tracker.complete_step("auth", "failed", f"Lỗi xác thực: {msg}")
        context.record_checkpoint("auth", "failed", error_message=msg)
        return _create_result("failed", "auth", error_message=f"Lỗi xác thực Fotello: {msg}")

    id_token = str(tokens.get("id_token") or tokens.get("access_token", ""))
    access_token = str(tokens.get("access_token") or tokens.get("id_token", ""))
    team_id = str(tokens.get("team_id", ""))
    if not team_id:
        tracker.complete_step("auth", "failed", "Không tìm thấy team_id trong token.")
        context.record_checkpoint("auth", "failed", error_message="Thiếu team_id")
        return _create_result("failed", "auth", error_message="Không tìm thấy team_id trong phiên đăng nhập.")
    tracker.complete_step("auth", "success", "Phiên làm việc hợp lệ.")

    is_restart_at_polling = (
        context.attempt_no > 1
        and prev_step in ("polling",)
    )
    is_restart_at_download = (
        context.attempt_no > 1
        and prev_step in ("download", "export")
    )

    if (is_restart_at_polling or is_restart_at_download) and (not listing_id or not output_enhance_map):
        err_msg = "Lỗi checkpoint: thiếu listing_id hoặc enhance_map để tiếp tục tác vụ."
        tracker.start_step(prev_step, "Đang khôi phục phiên xử lý từ checkpoint...")
        tracker.complete_step(prev_step, "failed", err_msg)
        context.record_checkpoint(prev_step, "failed", error_message=err_msg)
        return _create_result("failed", prev_step, error_message=err_msg)

    if not is_restart_at_polling and not is_restart_at_download:
        # Nhóm 1: Chạy từ đầu (hoặc restart trước polling)
        if context.is_cancelled():
            return _create_result("cancelled", "prepare", cancel_reason="stopped_during_execution")

        # 2. Kiểm tra toàn vẹn input
        tracker.start_step("prepare", "Đang kiểm tra toàn vẹn các file đầu vào...")
        context.record_checkpoint("prepare", "running")
        try:
            verify_inputs_integrity(spec.outputs)
        except BracketValidationError as b_err:
            tracker.complete_step("prepare", "failed", str(b_err))
            context.record_checkpoint("prepare", "failed", error_message=str(b_err))
            return _create_result("failed", "prepare", error_message=str(b_err))
        tracker.complete_step("prepare", "success", f"Đã xác thực toàn vẹn {len(spec.outputs)} bracket.")

        if context.is_cancelled():
            return _create_result("cancelled", "upload", cancel_reason="stopped_during_execution")

        # 3. Tải ảnh gốc (Upload)
        tracker.start_step("upload", "Đang tải ảnh gốc lên cloud storage...")
        context.record_checkpoint("upload", "running")

        output_upload_ids, failed_uploads = upload_job_inputs(
            outputs=spec.outputs,
            id_token=id_token,
            team_id=team_id,
            config=cfg,
            stop_event=context.stop_event,
            progress_fn=lambda cur, tot: tracker.progress_step("upload", f"Upload tiến độ: {cur}/{tot} file.", current=cur, total=tot),
            log_fn=context.log_fn,
        )

        if context.is_cancelled():
            tracker.complete_step("upload", "cancelled", "Upload bị dừng bởi người dùng.")
            context.record_checkpoint("upload", "cancelled", cancel_reason="stopped_during_execution")
            return _create_result("cancelled", "upload", cancel_reason="stopped_during_execution")

        # Quy tắc: nếu thiếu bất kỳ input nào sau retry -> dừng trước tạo listing
        if failed_uploads or len(output_upload_ids) < len(spec.outputs):
            err_msg = f"Upload không đủ file cho {len(spec.outputs) - len(output_upload_ids)} output. Dừng trước tạo listing."
            tracker.complete_step("upload", "failed", err_msg)
            context.record_checkpoint(
                "upload",
                "failed",
                server_resources={"uploaded_temp_ids": output_upload_ids},
                error_message=err_msg,
            )
            failed_outputs.extend([{"output_id": f["output_id"], "error": f["error"]} for f in failed_uploads])
            return _create_result(
                "failed",
                "upload",
                error_message=err_msg,
                extra_server_resources={"uploaded_temp_ids": output_upload_ids},
            )
        tracker.complete_step("upload", "success", f"Đã upload đầy đủ file cho {len(output_upload_ids)} outputs.")

        if context.is_cancelled():
            return _create_result("cancelled", "create_listing", cancel_reason="stopped_during_execution")

        # 4 & 5. Tạo Listing và Enhances
        prefix = spec.preferences.get("listing_name_prefix", cfg.preferences.listing_name_prefix)
        listing_name = f"{prefix} [{spec.run_id}_{spec.job_id}]"

        tracker.start_step("create_listing", "Đang tạo listing mới trên server...")
        context.record_checkpoint("create_listing", "running")

        try:
            listing_id, output_enhance_map = create_job_resources(
                listing_name=listing_name,
                outputs=spec.outputs,
                output_upload_ids=output_upload_ids,
                preferences=spec.preferences,
                team_id=team_id,
                id_token=id_token,
                config=cfg,
                log_fn=context.log_fn,
            )
            tracker.complete_step("create_listing", "success", f"Tạo listing thành công: {listing_id}")
            tracker.start_step("execute", "Đã gửi yêu cầu xử lý enhance.")
            tracker.complete_step("execute", "success", f"Đã gửi {len(output_enhance_map)} enhance.")
            context.record_checkpoint(
                "execute",
                "success",
                server_resources={"listing_id": listing_id, "enhance_map": output_enhance_map},
            )
        except Exception as exc:
            tracker.complete_step("create_listing", "failed", f"Lỗi tạo tài nguyên server: {exc}")
            context.record_checkpoint("create_listing", "failed", error_message=str(exc))
            return _create_result("failed", "create_listing", error_message=str(exc))
    else:
        # Bỏ qua các bước trước nếu restart tại polling hoặc download
        tracker.start_step("prepare", "Bỏ qua step prepare do restart.")
        tracker.complete_step("prepare", "success", "Đã phục hồi từ checkpoint.")
        tracker.start_step("upload", "Bỏ qua step upload do restart.")
        tracker.complete_step("upload", "success", "Đã phục hồi từ checkpoint.")
        tracker.start_step("create_listing", "Bỏ qua step tạo listing do restart.")
        tracker.complete_step("create_listing", "success", f"Dùng lại listing {listing_id}.")
        tracker.start_step("execute", "Bỏ qua step execute do restart.")
        tracker.complete_step("execute", "success", f"Dùng lại {len(output_enhance_map)} enhance.")

    # 6. Polling trạng thái
    target_enhance_ids = list(output_enhance_map.values())
    if not is_restart_at_download:
        tracker.start_step("polling", f"Đang theo dõi xử lý {len(target_enhance_ids)} enhance...")
        context.record_checkpoint("polling", "running")

        succeeded_e_ids, failed_e_ids, timed_out_e_ids = poll_enhances_completion(
            enhance_ids=target_enhance_ids,
            access_token=access_token,
            config=cfg,
            stop_event=context.stop_event,
            progress_fn=lambda cur, tot: tracker.progress_step("polling", f"Tiến độ AI: {cur}/{tot} enhance đã xong.", current=cur, total=tot),
            log_fn=context.log_fn,
        )

        if context.is_cancelled():
            tracker.complete_step("polling", "cancelled", "Polling bị dừng bởi người dùng.")
            context.record_checkpoint("polling", "cancelled", cancel_reason="stopped_during_execution")
            return _create_result("cancelled", "polling", cancel_reason="stopped_during_execution")

        if timed_out_e_ids:
            msg_fail = f"Hết thời gian chờ cho {len(timed_out_e_ids)} enhance."
            tracker.complete_step("polling", "failed", msg_fail)
            context.record_checkpoint("polling", "failed", error_message=msg_fail)
            return _create_result("failed", "polling", error_message=msg_fail)

        tracker.complete_step("polling", "success", f"AI đã hoàn thành {len(succeeded_e_ids)}/{len(target_enhance_ids)} enhance.")
        context.record_checkpoint("polling", "success")
    else:
        tracker.start_step("polling", "Bỏ qua step polling do restart tại download.")
        tracker.complete_step("polling", "success", "Tiếp tục tải file.")

    if context.is_cancelled():
        return _create_result("cancelled", "download", cancel_reason="stopped_during_execution")

    # 7. Tải ảnh thành phẩm (Download)
    enh_to_out = {v: k for k, v in output_enhance_map.items()}
    dl_target_count = len(enh_to_out)
    tracker.start_step("download", f"Đang tải {dl_target_count} ảnh thành phẩm từ cloud storage...", current=0, total=dl_target_count)
    context.record_checkpoint("download", "running")

    # Đọc lại thông tin listing và enhances từ Firestore để lấy link rendition
    try:
        remote_enhances = listings.list_enhances(
            listing_id,
            access_token=access_token,
            config=cfg,
            log_fn=context.log_fn,
            stop_event=context.stop_event,
        )
    except Exception as exc:
        tracker.complete_step("download", "failed", f"Lỗi đọc enhances Firestore: {exc}")
        context.record_checkpoint("download", "failed", error_message=str(exc))
        return _create_result("failed", "download", error_message=str(exc))

    remote_map = {item["enhance_id"]: item for item in remote_enhances if item.get("has_image")}
    previously_downloaded = set(context.server_resources.get("downloaded_files", []))

    for enh_id, out_id in enh_to_out.items():
        if context.is_cancelled():
            break

        item = remote_map.get(enh_id)
        if not item:
            failed_outputs.append({"output_id": out_id, "error": f"Không có ảnh thành phẩm cho enhance {enh_id}"})
            continue

        dest_filename = f"{out_id}.jpg"
        target_path = out_dir / dest_filename
        temp_path = out_dir / f".tmp_{dest_filename}"

        has_download_tracking = (
            "downloaded_files" in context.server_resources
            or (context.previous_attempt and "downloaded_files" in context.previous_attempt.server_resources)
        )
        is_recorded = (out_id in previously_downloaded) if has_download_tracking else True

        can_reuse = False
        if is_restart_at_download and is_recorded and target_path.is_file() and target_path.stat().st_size > 0:
            try:
                with Image.open(target_path) as img_check:
                    img_check.verify()
                can_reuse = True
            except Exception:
                can_reuse = False

        if can_reuse:
            succeeded_outputs.append(out_id)
            done_count = len(succeeded_outputs)
            tracker.progress_step(
                "download",
                f"Đã xác nhận có sẵn {out_id} ({done_count}/{dl_target_count})",
                current=done_count,
                total=dl_target_count,
            )
            continue

        try:
            file_bytes, sha256_hex, (w, h), ext = client.stream_download_image(
                uri=item["image_uri"],
                access_token=access_token,
                dest_temp_path=temp_path,
                max_bytes=cfg.download.max_file_bytes,
                max_dimension=cfg.download.max_image_dimension,
                stop_event=context.stop_event,
            )
            temp_path.replace(target_path)
            succeeded_outputs.append(out_id)
            downloaded_records.append({
                "output_id": out_id,
                "enhance_id": enh_id,
                "rendition": item.get("rendition"),
                "file_path": str(target_path),
                "width": w,
                "height": h,
                "sha256": sha256_hex,
            })
            current_downloaded = list(previously_downloaded)
            if out_id not in current_downloaded:
                current_downloaded.append(out_id)
            context.record_checkpoint(
                "download",
                "running",
                server_resources={
                    "listing_id": listing_id,
                    "enhance_map": output_enhance_map,
                    "downloaded_files": current_downloaded,
                },
            )
            done_count = len(succeeded_outputs)
            tracker.progress_step(
                "download",
                f"Đã tải thành công {out_id} ({done_count}/{dl_target_count})",
                current=done_count,
                total=dl_target_count,
            )
        except Exception as exc:
            failed_outputs.append({"output_id": out_id, "error": str(exc)})
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

    if context.is_cancelled():
        tracker.complete_step("download", "cancelled", f"Tác vụ bị dừng. Đã tải {len(succeeded_outputs)}/{dl_target_count} ảnh.")
        context.record_checkpoint("download", "cancelled", cancel_reason="stopped_during_execution")
        return _create_result("cancelled", "download", cancel_reason="stopped_during_execution")

    if len(succeeded_outputs) >= dl_target_count and len(failed_outputs) == 0:
        tracker.complete_step("download", "success", f"Đã tải đủ toàn bộ {len(succeeded_outputs)}/{dl_target_count} ảnh.")
        context.record_checkpoint("download", "success", server_resources={"listing_id": listing_id, "enhance_map": output_enhance_map})
    elif len(succeeded_outputs) > 0:
        tracker.complete_step("download", "partial", f"Tải hoàn tất {len(succeeded_outputs)}/{dl_target_count} ảnh ({len(failed_outputs)} lỗi).")
        context.record_checkpoint("download", "partial", server_resources={"listing_id": listing_id, "enhance_map": output_enhance_map})
    else:
        tracker.complete_step("download", "failed", "Không tải được ảnh thành phẩm nào về thư mục đích.")
        context.record_checkpoint("download", "failed", server_resources={"listing_id": listing_id, "enhance_map": output_enhance_map})

    # 8. Xuất manifest của Job (Export)
    tracker.start_step("export", "Đang hoàn tất tác vụ...")
    if len(succeeded_outputs) >= len(spec.outputs) and len(failed_outputs) == 0:
        job_status = "success"
        tracker.complete_step("export", "success", f"Hoàn tất 100% ({len(succeeded_outputs)}/{len(spec.outputs)} outputs).")
    elif len(succeeded_outputs) > 0:
        job_status = "partial"
        tracker.complete_step("export", "partial", f"Hoàn thành một phần: {len(succeeded_outputs)}/{len(spec.outputs)} outputs.")
    else:
        job_status = "failed"
        tracker.complete_step("export", "failed", "Không tải được output nào thành công.")

    context.record_checkpoint("export", job_status)
    return _create_result(job_status, "export")


def run_workflow(
    *,
    input_dir: Path | str,
    output_dir: Path | str,
    mode: str = "single",
    bracket_size: int | None = None,
    preferences: dict[str, Any] | None = None,
    stop_event: Any | None = None,
    event_fn: Callable[[StepEvent], None] | None = None,
    log_fn: Callable[[str, str], None] | None = None,
    store: JobStore | None = None,
    run_id: str | None = None,
) -> BatchResult:
    """Entrypoint chính chạy quy trình Fotello hoàn chỉnh (single hoặc batch).

    Args:
        input_dir: Thư mục chứa ảnh gốc.
        output_dir: Thư mục lưu kết quả ảnh tải về.
        mode: 'single' (tối đa 1 job) hoặc 'batch' (tự chia nhiều job tuần tự).
        bracket_size: Số ảnh mỗi bracket (1, 3, hoặc 5). Mặc định lấy từ config.
        preferences: Ghi đè thông số ảnh (contrast_style, cloud_style, ...).
        stop_event: Tín hiệu dừng từ caller.
        event_fn: Callback nhận StepEvent.
        log_fn: Callback ghi log.
        store: Checkpoint store tùy chọn.
        run_id: ID phiên chạy tùy chọn.

    Returns:
        BatchResult tổng hợp trạng thái và kết quả các jobs.
    """
    cfg = load_fotello_config()
    prefs = asdict(cfg.preferences)
    if preferences:
        prefs.update(preferences)
    if bracket_size is not None:
        prefs["bracket_size"] = bracket_size

    # Nhóm bracket và kiểm tra trước khi upload
    outputs = build_bracket_outputs(
        input_paths=[input_dir],
        bracket_size=int(prefs["bracket_size"]),
    )

    # Lập kế hoạch job theo giới hạn cấu hình
    plan = plan_jobs(
        engine="fotello",
        outputs=outputs,
        mode=mode,
        limits=cfg.jobs.to_limits(),
        preferences=prefs,
        output_dir=output_dir,
        run_id=run_id,
    )

    # Chạy các job tuần tự qua runner dùng chung
    return run_jobs(
        plan=plan,
        execute_job=fotello_executor,
        stop_event=stop_event,
        event_fn=event_fn,
        log_fn=log_fn,
        store=store,
    )


def restart_workflow_job(
    *,
    run_id: str,
    job_id: str,
    stop_event: Any | None = None,
    event_fn: Callable[[StepEvent], None] | None = None,
    log_fn: Callable[[str, str], None] | None = None,
    store: JobStore | None = None,
) -> JobResult:
    """Entrypoint chạy lại (manual restart) một job cụ thể trong một run."""
    return restart_job(
        run_id=run_id,
        job_id=job_id,
        execute_job=fotello_executor,
        stop_event=stop_event,
        event_fn=event_fn,
        log_fn=log_fn,
        store=store,
    )
