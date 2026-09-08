"""State and naming helpers shared by automatic and manual workflows."""

from .cleaner import (
    apply_watermark_positions_to_manifest,
    clean_output,
    compare_variant_pair,
    update_manifest_watermark_positions,
)
from .models import (
    MANIFEST_VERSION,
    VALID_BRACKET_SIZES,
    WorkflowValidationError,
    attempt_name,
    build_groups,
    new_manifest,
    parse_attempt_name,
    sanitize_output_stem,
    sanitize_prefix,
    summary,
)
from .store import ManifestStore

__all__ = [
    "MANIFEST_VERSION",
    "VALID_BRACKET_SIZES",
    "WorkflowValidationError",
    "apply_watermark_positions_to_manifest",
    "attempt_name",
    "build_groups",
    "clean_output",
    "compare_variant_pair",
    "new_manifest",
    "ManifestStore",
    "parse_attempt_name",
    "sanitize_output_stem",
    "sanitize_prefix",
    "summary",
    "update_manifest_watermark_positions",
]
