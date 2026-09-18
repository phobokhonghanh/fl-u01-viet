"""Data models for Fotello engine and rendition tracking."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RenditionInfo:
    """Detailed rendition tracking for an enhanced image."""
    enhance_id: str
    rendition: str
    gs_uri: str
    original_filename: str = ""
    source_width: int = 0
    source_height: int = 0
    downloaded_width: int = 0
    downloaded_height: int = 0
    final_width: int = 0
    final_height: int = 0
    file_bytes: int = 0
    is_upsized: bool = False
    is_local_resized: bool = False
    resolution_shortfall: bool = False
    error: str | None = None
    variant_id: str | None = None
    render_id: str | None = None
    field_name: str | None = None
    sha256: str | None = None
    local_path: Path | None = None
    output_filename: str | None = None
    requested_rendition: str = ""
    fallback_reason: str | None = None


@dataclass
class DownloadResult:
    """Result of a download operation with per-file audit details."""
    success: bool
    status: str  # "success", "partial", "failed", "cancelled"
    count: int
    files: list[Path] = field(default_factory=list)
    renditions: list[RenditionInfo] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    message: str = ""
    manifest_path: Path | None = None
