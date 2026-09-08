"""Manifest watermark position synchronization and extraction for workflow."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Sequence

from backend.watermark_cleaner.config import WatermarkCleanerConfig
from .comparison import compare_variant_pair


def _normalise_output_name(output_name: str | Path) -> str:
    """Return a safe, lossless output filename.

    Workflow manifests normally carry ``img01`` or ``img01.png``. Keeping a
    supplied PNG/TIFF suffix makes the function friendly to callers that have
    already normalised names; a bare stem receives the configured PNG suffix
    at the publication boundary.
    """
    name = Path(str(output_name)).name.strip()
    if not name or name in {".", ".."}:
        return ""

    suffix = Path(name).suffix.lower()
    if suffix in {".png", ".tif", ".tiff"}:
        return name
    return f"{Path(name).stem or name}.png"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 4:
                    path.write_text(
                        json.dumps(payload, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    break
                import time
                time.sleep(0.05)
    finally:
        temporary.unlink(missing_ok=True)


def _extract_variant_watermarks(
    comparisons: Sequence[Mapping[str, Any]],
    attempts: Sequence[Mapping[str, Any]],
    cleaner_payload: Mapping[str, Any] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Extract detected watermark corner, box, percentages, and display per source image path.

    Returns:
        (variant_watermarks, watermark_positions)
        where variant_watermarks maps normalized path string to metadata:
            {"corner": "BL", "box": [x1, y1, x2, y2], "confidence": 0.95, "confidence_percent": 95.0,
             "watermark_ratio": 0.0845, "watermark_percent": 8.45, "display": "BL (8.45%)"}
        and watermark_positions maps path/filename to detail dict:
            {"photo01.jpg": {"corner": "BL", "watermark_percent": 8.45, "confidence_percent": 95.0, "display": "BL (8.45%)"}}
    """
    variant_watermarks: dict[str, dict[str, Any]] = {}

    def _record_source(
        src: Any,
        corner: str | None,
        box: Any = None,
        conf: Any = None,
        ratio: Any = None,
    ) -> None:
        if not src:
            return
        try:
            norm = str(Path(src).resolve())
        except Exception:
            norm = str(src)
        existing = variant_watermarks.get(norm)

        ratio_val = float(ratio) if ratio is not None else (existing.get("watermark_ratio", 0.0) if existing else 0.0)
        conf_val = float(conf) if conf is not None else (existing.get("confidence", 1.0) if existing else 1.0)
        wm_pct = round(ratio_val * 100.0, 2)
        conf_pct = round(conf_val * 100.0, 2)
        corner_str = str(corner) if corner and str(corner).upper() not in {"NONE", "CLEAN"} else None
        disp = f"{corner_str} ({wm_pct:.2f}%)" if corner_str else "NONE"

        if (
            existing is None
            or (box is not None and existing.get("box") is None)
            or (ratio is not None and existing.get("watermark_ratio", 0.0) == 0.0)
        ):
            variant_watermarks[norm] = {
                "corner": corner_str,
                "box": list(box) if box is not None else (existing.get("box") if existing else None),
                "confidence": conf_val,
                "confidence_percent": conf_pct,
                "watermark_ratio": ratio_val,
                "watermark_percent": wm_pct,
                "display": disp,
            }

    # 1. From cleaner_payload (if direct clean_case result available)
    if cleaner_payload and isinstance(cleaner_payload, Mapping):
        analyses = cleaner_payload.get("corner_analyses")
        if isinstance(analyses, Mapping):
            for corner_key, analysis in analyses.items():
                if not isinstance(analysis, Mapping):
                    continue
                c_name = str(analysis.get("corner") or corner_key)
                c_box = analysis.get("box")
                c_conf = analysis.get("confidence")
                c_metrics = analysis.get("metrics") or {}
                c_ratios = c_metrics.get("watermark_ratios") or {}
                sig_ratio = c_metrics.get("significant_ratio") or 0.0
                for s in analysis.get("watermarked_sources", []) or []:
                    s_name = Path(str(s)).name
                    r = c_ratios.get(s_name, sig_ratio)
                    _record_source(s, c_name, c_box, c_conf, r)

    # 2. From cleaner_attempts
    for att in attempts:
        if not isinstance(att, Mapping):
            continue
        c_info = att.get("cleaner")
        if isinstance(c_info, Mapping):
            analyses = c_info.get("corner_analyses")
            if isinstance(analyses, Mapping):
                for corner_key, analysis in analyses.items():
                    if not isinstance(analysis, Mapping):
                        continue
                    c_name = str(analysis.get("corner") or corner_key)
                    c_box = analysis.get("box")
                    c_conf = analysis.get("confidence")
                    c_metrics = analysis.get("metrics") or {}
                    c_ratios = c_metrics.get("watermark_ratios") or {}
                    sig_ratio = c_metrics.get("significant_ratio") or 0.0
                    for s in analysis.get("watermarked_sources", []) or []:
                        s_name = Path(str(s)).name
                        r = c_ratios.get(s_name, sig_ratio)
                        _record_source(s, c_name, c_box, c_conf, r)

    # 3. From comparisons
    for comp in comparisons:
        if not isinstance(comp, Mapping):
            continue
        detector = comp.get("detector")
        if isinstance(detector, Mapping):
            for corner_key, info in detector.items():
                if not isinstance(info, Mapping):
                    continue
                c_box = info.get("box")
                c_conf = info.get("confidence")
                c_metrics = info.get("metrics") or {}
                c_ratios = c_metrics.get("watermark_ratios") or {}
                sig_ratio = c_metrics.get("significant_ratio") or 0.0
                for s in info.get("watermarked_sources", []) or []:
                    s_name = Path(str(s)).name
                    r = c_ratios.get(s_name, sig_ratio)
                    _record_source(s, str(corner_key), c_box, c_conf, r)
        # Also check duplicate pair where single changed corner was identified
        if comp.get("status") == "duplicate":
            changed = list(comp.get("changed_corners", []) or [])
            if len(changed) == 1:
                dup_corner = str(changed[0])
                first_src = comp.get("first")
                second_src = comp.get("second")
                det_dup = (detector.get(dup_corner) or {}) if isinstance(detector, Mapping) else {}
                dup_box = det_dup.get("box")
                dup_conf = det_dup.get("confidence")
                dup_metrics = det_dup.get("metrics") or {}
                dup_ratios = dup_metrics.get("watermark_ratios") or {}
                dup_sig_ratio = dup_metrics.get("significant_ratio") or 0.0
                if first_src:
                    r1 = dup_ratios.get(Path(str(first_src)).name, dup_sig_ratio)
                    _record_source(first_src, dup_corner, dup_box, dup_conf, r1)
                if second_src:
                    r2 = dup_ratios.get(Path(str(second_src)).name, dup_sig_ratio)
                    _record_source(second_src, dup_corner, dup_box, dup_conf, r2)

    watermark_positions: dict[str, Any] = {}
    for norm_path, info in variant_watermarks.items():
        entry = {
            "corner": info.get("corner"),
            "watermark_percent": info.get("watermark_percent", 0.0),
            "confidence_percent": info.get("confidence_percent", 0.0),
            "display": info.get("display", "NONE"),
        }
        path_obj = Path(norm_path)
        watermark_positions[norm_path] = dict(entry)
        watermark_positions[f"{path_obj.parent.name}/{path_obj.name}"] = dict(entry)
        if path_obj.name not in watermark_positions:
            watermark_positions[path_obj.name] = dict(entry)

    return variant_watermarks, watermark_positions


def apply_watermark_positions_to_manifest(
    manifest: dict[str, Any] | None,
    group: dict[str, Any] | None,
    cleaner_result: Mapping[str, Any] | None,
) -> bool:
    """Update watermark position per image in group variants and attempt records.

    Updates for each variant in ``group['variants']``:
      - ``watermark_corner``: Corner name e.g. 'BL'
      - ``watermark_position``: Same as watermark_corner
      - ``watermark_box``: Bounding box [x1, y1, x2, y2] if known
      - ``watermark_confidence``: Confidence float if known

    Updates for ``group``:
      - ``watermark_positions``: Mapping of variant filename / path to corner name

    Updates for matching records in ``manifest['attempts']``:
      - ``watermark_corner``
      - ``watermark_position``
      - ``watermark_box``
    """
    if not isinstance(cleaner_result, Mapping):
        return False
    if not isinstance(group, dict) and not isinstance(manifest, dict):
        return False

    if isinstance(manifest, dict) and (not isinstance(group, dict) or not group):
        out_id = str(cleaner_result.get("output_id") or "")
        if out_id:
            for g in manifest.get("groups", []) or []:
                if isinstance(g, dict) and g.get("output_id") == out_id:
                    group = g
                    break

    variant_watermarks = cleaner_result.get("variant_watermarks")
    watermark_positions = cleaner_result.get("watermark_positions")

    if not isinstance(variant_watermarks, dict) or not variant_watermarks:
        variant_watermarks, extracted_positions = _extract_variant_watermarks(
            comparisons=cleaner_result.get("comparisons", []) or [],
            attempts=cleaner_result.get("cleaner_attempts", []) or [],
            cleaner_payload=cleaner_result.get("cleaner"),
        )
        if not isinstance(watermark_positions, dict) or not watermark_positions:
            watermark_positions = extracted_positions

    updated_any = False
    variants = group.get("variants", []) if isinstance(group, dict) else []

    def _match_info(path_val: Any) -> tuple[str | None, list[int] | None, float, float, str]:
        if not path_val:
            return None, None, 100.0, 0.0, "NONE"
        path_obj = Path(str(path_val))
        try:
            norm_resolved = str(path_obj.resolve())
        except Exception:
            norm_resolved = str(path_val)
        raw_str = str(path_val)
        name = path_obj.name

        info = (
            variant_watermarks.get(norm_resolved)
            or variant_watermarks.get(raw_str)
            or variant_watermarks.get(name)
        )
        if isinstance(info, dict):
            corner = info.get("corner")
            box = info.get("box")
            conf_pct = float(info.get("confidence_percent", round(float(info.get("confidence") or 1.0) * 100.0, 2)))
            wm_pct = float(info.get("watermark_percent", round(float(info.get("watermark_ratio") or 0.0) * 100.0, 2)))
            disp = info.get("display") or (f"{corner} ({wm_pct:.2f}%)" if corner else "NONE")
            return (
                str(corner) if corner else None,
                list(box) if box is not None else None,
                conf_pct,
                wm_pct,
                str(disp),
            )
        if isinstance(watermark_positions, dict):
            item = (
                watermark_positions.get(norm_resolved)
                or watermark_positions.get(raw_str)
                or watermark_positions.get(name)
            )
            if isinstance(item, dict):
                corner = item.get("corner")
                box = item.get("box")
                conf_pct = float(item.get("confidence_percent", 100.0))
                wm_pct = float(item.get("watermark_percent", 0.0))
                disp = item.get("display") or (f"{corner} ({wm_pct:.2f}%)" if corner else "NONE")
                return (
                    str(corner) if corner else None,
                    list(box) if box is not None else None,
                    conf_pct,
                    wm_pct,
                    str(disp),
                )
            elif isinstance(item, str) and item and item.upper() not in {"NONE", "CLEAN"}:
                return str(item), None, 100.0, 0.0, f"{item} (0.00%)"
        return None, None, 100.0, 0.0, "NONE"

    if isinstance(variants, list):
        for v in variants:
            if not isinstance(v, dict):
                continue
            corner, box, conf_pct, wm_pct, disp = _match_info(v.get("path"))
            if corner:
                v["watermark_corner"] = corner
                v["watermark_position"] = corner
                if box is not None:
                    v["watermark_box"] = box
                v["watermark_percent"] = wm_pct
                v["confidence_percent"] = conf_pct
                v["watermark_display"] = disp
                updated_any = True
            else:
                v["watermark_corner"] = None
                v["watermark_position"] = None
                v["watermark_box"] = None
                v["watermark_percent"] = 0.0
                v["confidence_percent"] = 100.0
                v["watermark_display"] = "NONE"

    if isinstance(group, dict):
        if "watermark_positions" not in group or not isinstance(group["watermark_positions"], dict):
            group["watermark_positions"] = {}
        for v in variants:
            if isinstance(v, dict) and v.get("path"):
                p = Path(str(v["path"]))
                entry = {
                    "corner": v.get("watermark_corner"),
                    "watermark_percent": v.get("watermark_percent", 0.0),
                    "confidence_percent": v.get("confidence_percent", 100.0),
                    "display": v.get("watermark_display", "NONE"),
                }
                group["watermark_positions"][str(v["path"])] = dict(entry)
                group["watermark_positions"][f"{p.parent.name}/{p.name}"] = dict(entry)
                if v.get("enhance_id"):
                    group["watermark_positions"][str(v["enhance_id"])] = dict(entry)
                if p.name not in group["watermark_positions"]:
                    group["watermark_positions"][p.name] = dict(entry)

    # Also sync into manifest attempts records
    if isinstance(manifest, dict):
        attempts = manifest.get("attempts", [])
        if isinstance(attempts, list):
            for attempt in attempts:
                if not isinstance(attempt, dict):
                    continue
                records = attempt.get("records", [])
                if not isinstance(records, list):
                    continue
                for record in records:
                    if not isinstance(record, dict):
                        continue
                    if group and group.get("output_id") and record.get("output_id") and record.get("output_id") != group.get("output_id"):
                        continue
                    rec_path = record.get("path")
                    corner, box, conf_pct, wm_pct, disp = _match_info(rec_path)
                    if not corner and isinstance(variants, list):
                        # Match by enhance_id
                        rec_eid = record.get("enhance_id")
                        for v in variants:
                            if isinstance(v, dict) and rec_eid and v.get("enhance_id") == rec_eid and v.get("watermark_corner"):
                                corner = v.get("watermark_corner")
                                box = v.get("watermark_box")
                                conf_pct = v.get("confidence_percent", 100.0)
                                wm_pct = v.get("watermark_percent", 0.0)
                                disp = v.get("watermark_display", "NONE")
                                break
                    if corner:
                        record["watermark_corner"] = corner
                        record["watermark_position"] = corner
                        if box is not None:
                            record["watermark_box"] = box
                        record["watermark_percent"] = wm_pct
                        record["confidence_percent"] = conf_pct
                        record["watermark_display"] = disp
                        updated_any = True
                    else:
                        record["watermark_corner"] = None
                        record["watermark_position"] = None
                        record["watermark_box"] = None
                        record["watermark_percent"] = 0.0
                        record["confidence_percent"] = 100.0
                        record["watermark_display"] = "NONE"

    return updated_any


def update_manifest_watermark_positions(
    manifest_or_path: dict[str, Any] | str | Path,
    *,
    config: WatermarkCleanerConfig | None = None,
) -> dict[str, Any]:
    """Inspect variant files for all groups in a manifest and update watermark positions.

    If manifest_or_path is a path or string, loads the manifest, applies detected
    watermark positions for each image in all groups, and saves the updated manifest
    back to disk.
    """
    from .store import ManifestStore

    cfg = config or WatermarkCleanerConfig()
    from_file = False
    manifest_path: Path | None = None

    if isinstance(manifest_or_path, (str, Path)):
        from_file = True
        manifest_path = Path(manifest_or_path).resolve()
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Manifest not found at {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError(f"Manifest at {manifest_path} must be a JSON object")
    elif isinstance(manifest_or_path, dict):
        manifest = manifest_or_path
    else:
        raise TypeError(f"manifest_or_path must be a dict or path, got {type(manifest_or_path).__name__}")

    output_dir = Path(str(manifest.get("output_dir") or (manifest_path.parent if manifest_path else ".")))
    groups = manifest.get("groups", [])
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict):
                continue
            variants = group.get("variants", [])
            if not isinstance(variants, list) or not variants:
                continue
            paths = [
                Path(v["path"]) for v in variants
                if isinstance(v, dict) and v.get("path") and Path(v["path"]).is_file()
            ]
            if len(paths) < 2:
                continue

            output_name = group.get("output_name") or group.get("output_id") or ""
            filename = _normalise_output_name(str(output_name))
            report_path = output_dir / "reports" / f"{Path(filename).stem}.json"
            clean_result: dict[str, Any] | None = None
            if report_path.is_file():
                try:
                    clean_result = json.loads(report_path.read_text(encoding="utf-8"))
                except Exception:
                    clean_result = None

            if clean_result is None or ("variant_watermarks" not in clean_result and "cleaner" not in clean_result):
                first_path, second_path = paths[0], paths[1]
                comparison = compare_variant_pair(first_path, second_path, config=cfg)
                clean_result = {
                    "comparisons": [dict(comparison)],
                }

            apply_watermark_positions_to_manifest(manifest, group, clean_result)

    if from_file and manifest_path:
        try:
            store = ManifestStore(manifest_path.parent)
            store.save(manifest)
        except Exception:
            _atomic_json(manifest_path, manifest)

    return manifest


__all__ = [
    "_atomic_json",
    "_extract_variant_watermarks",
    "_normalise_output_name",
    "apply_watermark_positions_to_manifest",
    "update_manifest_watermark_positions",
]
