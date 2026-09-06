"""Data models and report structures for watermark cleaner pipeline."""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any


class CornerName(str, Enum):
    """The four corners where watermarks may appear."""
    TL = "TL"
    TR = "TR"
    BL = "BL"
    BR = "BR"


@dataclass(frozen=True)
class CornerRegion:
    """Bounding box coordinates for a corner ROI."""
    name: CornerName
    box: tuple[int, int, int, int]  # (left, top, right, bottom)

    @property
    def width(self) -> int:
        return self.box[2] - self.box[0]

    @property
    def height(self) -> int:
        return self.box[3] - self.box[1]

    @property
    def area(self) -> int:
        return self.width * self.height


@dataclass
class CornerAnalysis:
    """Analysis result for a single corner ROI across candidate images."""
    corner: CornerName
    box: tuple[int, int, int, int]
    status: str  # "clean_all", "resolved", "ambiguous", "duplicate_watermarked", "uncovered"
    clean_source: str | None = None
    watermarked_sources: list[str] = field(default_factory=list)
    confidence: float = 1.0
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "corner": self.corner.value,
            "box": list(self.box),
            "status": self.status,
            "clean_source": self.clean_source,
            "watermarked_sources": self.watermarked_sources,
            "confidence": round(self.confidence, 4),
            "metrics": self.metrics,
        }


@dataclass
class ReplacedRegion:
    """Record of a region replaced in the composite clean image."""
    corner: CornerName
    box: tuple[int, int, int, int]
    source: str
    confidence: float
    full_roi: tuple[int, int, int, int] | None = None
    clean_source: str | None = None
    seam_metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "corner": self.corner.value,
            "box": list(self.box),
            "full_roi": list(self.full_roi) if self.full_roi else list(self.box),
            "source": self.source,
            "clean_source": self.clean_source or self.source,
            "confidence": round(self.confidence, 4),
            "seam_metrics": self.seam_metrics,
        }


@dataclass
class CleaningResult:
    """Complete result and report for a single case cleaning execution."""
    case_name: str
    case_dir: str
    success: bool
    timestamp: str
    status: str = "failed"  # complete, preview, or failed
    completion_percentage: float = 0.0
    quality_score_percentage: float = 0.0
    quality_metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    dimensions: tuple[int, int] | None = None
    mode: str | None = None
    base_image: str | None = None
    source_images: list[str] = field(default_factory=list)
    output_path: str | None = None
    report_path: str | None = None
    corner_analyses: dict[str, Any] = field(default_factory=dict)
    regions_replaced: list[dict[str, Any]] = field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_name": self.case_name,
            "case_dir": self.case_dir,
            "success": self.success,
            "timestamp": self.timestamp,
            "status": self.status,
            "completion_percentage": round(self.completion_percentage, 2),
            "quality_score_percentage": round(self.quality_score_percentage, 2),
            "quality_metrics": self.quality_metrics,
            "warnings": self.warnings,
            "dimensions": list(self.dimensions) if self.dimensions else None,
            "mode": self.mode,
            "base_image": self.base_image,
            "source_images": self.source_images,
            "output_path": self.output_path,
            "report_path": self.report_path,
            "corner_analyses": self.corner_analyses,
            "regions_replaced": self.regions_replaced,
            "error_type": self.error_type,
            "error_message": self.error_message,
        }


@dataclass
class BatchResult:
    """Aggregated result for batch processing across multiple cases."""
    total_cases: int
    succeeded_cases: int
    failed_cases: int
    results: list[CleaningResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "succeeded_cases": self.succeeded_cases,
            "failed_cases": self.failed_cases,
            "results": [r.to_dict() for r in self.results],
        }
