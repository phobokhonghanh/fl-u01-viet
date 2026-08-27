from __future__ import annotations

import base64


_XOR_KEY = b"Ft2026Obf"

CLIENT_VERSION = "2.2"


def _dec(blob: str) -> str:
    raw = base64.b64decode(blob)
    return bytes(b ^ _XOR_KEY[i % len(_XOR_KEY)] for i, b in enumerate(raw)).decode()


FIREBASE_API_KEY = _dec("Bz1IUWFPDlsoCSwBYwEFHQM1IAR/QAIGPSAuPiZAcwpYeBAtB0Vd")
FIREBASE_PROJECT = _dec("NBFTXB9TPBYHMhEfVltEKgAHNREfBAMGdgc=")
FIREBASE_AUTH_URL = f"https://securetoken.googleapis.com/v1/token?key={FIREBASE_API_KEY}"
FIRESTORE_URL = (
    f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT}"
    "/databases/(default)/documents"
)
FOTELLO_API = "https://api.fotello.co/v1"
PREPARE_DOWNLOAD_URL = "https://us-central1-real-estate-firebase-4109e.cloudfunctions.net/prepareDownload"

FLD_IS_WM = _dec("LwdlUUZTPQ8HNB9XVA==")
FLD_BV = _dec("JBtdXFdXITQHKgFX")
FLD_SV = _dec("NQBAWVxRGQMKMxE=")
FLD_ENHANCES = _dec("IxpaUVxVKhE=")
FLD_EDITED_UPSIZED = _dec("IxBbRFdSBg8HIRFnQEFfNQcC")
FLD_EDITED = _dec("IxBbRFdSBg8HIRE=")
FLD_STATUS = _dec("NQBTREdF")
EP_CREATE_LISTING = _dec("JQZXUUZTYg4PNQBbXlU=")
EP_CREATE_UPLOAD = _dec("JQZXUUZTYhcWKhtTVA==")
EP_CREATE_ENHANCE = _dec("JQZXUUZTYgcILhVcU1c=")

IMAGE_EXTENSIONS = {
    ".jpeg",
    ".webp",
    ".arw",
    ".jpg",
    ".cr3",
    ".cr2",
    ".tiff",
    ".tif",
    ".dng",
    ".nef",
    ".png",
    ".bmp",
}

CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".bmp": "image/bmp",
    ".cr3": "image/x-canon-cr3",
    ".cr2": "image/x-canon-cr2",
    ".nef": "image/x-nikon-nef",
    ".arw": "image/x-sony-arw",
    ".dng": "image/x-adobe-dng",
}

MAX_RETRIES = 5
POLL_INITIAL_INTERVAL = 60
POLL_LATER_INTERVAL = 30
POLL_INITIAL_ATTEMPTS = 10
POLL_READY_DIVISOR = 2
POLL_TIMEOUT = 1800
