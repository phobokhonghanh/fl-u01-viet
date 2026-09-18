"""Fotello Engine Package.

Provides clean public API for Fotello:
- Token management & session validation (auth)
- Chrome DevTools Protocol login (cdp)
- Configuration (config)
- Protocol constants (constants)
- Firestore client (firestore)
- Listing & enhance retrieval (listings)
- Rendition analysis & resolution selection (renditions)
- High-resolution rendition download (download)
- Bracket input grouping & verification (brackets)
- Resumable image upload (upload)
- Listing & enhance creation (execute)
- Firestore status polling (polling)
- End-to-end workflow & step-aware execution (workflow)
"""
from __future__ import annotations

from core.fotello import (
    auth,
    brackets,
    cdp,
    config,
    constants,
    download,
    execute,
    firestore,
    listings,
    polling,
    renditions,
    upload,
    workflow,
)
from core.fotello.models import DownloadResult, RenditionInfo

__all__ = [
    "auth",
    "brackets",
    "cdp",
    "config",
    "constants",
    "download",
    "execute",
    "firestore",
    "listings",
    "polling",
    "renditions",
    "upload",
    "workflow",
    "DownloadResult",
    "RenditionInfo",
]
