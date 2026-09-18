"""Constants for Fotello from_debug engine."""
from __future__ import annotations

import base64
from pathlib import Path
from core.shared.config import get_engine_dir, get_tokens_path

_XOR_KEY = b"Ft2026Obf"


def _dec(blob: str) -> str:
    raw = base64.b64decode(blob)
    return "".join(chr(b ^ _XOR_KEY[i % len(_XOR_KEY)]) for i, b in enumerate(raw))


FIREBASE_API_KEY: str = _dec("Bz1IUWFPDlsoCSwBYwEFHQM1IAR/QAIGPSAuPiZAcwpYeBAtB0Vd")
FIREBASE_PROJECT_ID: str = _dec("NBFTXB9TPBYHMhEfVltEKgAHNREfBAMGdgc=")

FLD_BV: str = _dec("JBtdXFdXITQHKgFX")                  # booleanValue
FLD_SV: str = _dec("NQBAWVxRGQMKMxE=")                  # stringValue
FLD_EDITED: str = _dec("IxBbRFdSBg8HIRE=")              # editedImage
FLD_EDITED_UPSIZED: str = _dec("IxBbRFdSBg8HIRFnQEFfNQcC")  # editedImageUpsized
FLD_ENHANCES: str = _dec("IxpaUVxVKhE=")                # enhances
FLD_IS_WM: str = _dec("LwdlUUZTPQ8HNB9XVA==")           # isWatermarked
FLD_STATUS: str = _dec("NQBTREdF")                      # status

FIRESTORE_URL: str = f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/databases/(default)/documents"
FIREBASE_AUTH_URL: str = f"https://securetoken.googleapis.com/v1/token?key={FIREBASE_API_KEY}"
FOTELLO_API: str = "https://app.fotello.co/api"

# Shared storage paths
ENGINE_DIR: Path = get_engine_dir("fotello")
TOKENS_FILE: Path = get_tokens_path("fotello")

DOWNLOAD_STEPS = [
    ("auth", "Auth"),
    ("listings", "Listings"),
    ("resolve_outputs", "ResolveOutputs"),
    ("download", "Download"),
    ("validate", "Validate"),
    ("export", "Export"),
]
