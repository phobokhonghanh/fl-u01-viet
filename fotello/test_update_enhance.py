#!/usr/bin/env python3
"""
Test script for endpoint: update-enhance
Endpoint: POST https://api.fotello.co/v1/update-enhance
Payload: {"id": enhance_id, "isWatermarked": False}
"""

import sys
import json
import argparse
from pathlib import Path
import urllib.request
import urllib.error

# Support importing from backend if running within fotello package
try:
    from backend.fotello_api import api_post
    from backend.client import print_system_exception
except ImportError:
    try:
        from fotello.backend.fotello_api import api_post
        from fotello.backend.client import print_system_exception
    except ImportError:
        api_post = None

        def print_system_exception(context: str, exc: BaseException | None = None) -> None:
            if exc is None:
                print(f"[Fotello][EXCEPTION] {context}")
            else:
                print(f"[Fotello][EXCEPTION] {context}: {type(exc).__name__}: {exc}")


FOTELLO_API = "https://api.fotello.co/v1"
DEFAULT_TOKEN_FILE = Path.home() / ".fotello_tokens_autohdr.json"


def api_post_standalone(endpoint: str, body: dict, id_token: str) -> dict:
    """Fallback standalone api_post implementation using urllib."""
    url = f"{FOTELLO_API}/{endpoint}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "authorization": id_token,
            "Origin": "https://app.fotello.co",
            "Referer": "https://app.fotello.co/",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        res_body = resp.read().decode("utf-8")
        return json.loads(res_body) if res_body else {}


def load_id_token(config_path: Path | str | None = None) -> str:
    """Load id_token from a JSON config file."""
    paths_to_try: list[Path] = []
    if config_path:
        paths_to_try.append(Path(config_path))

    # Common local JSON config file paths
    script_dir = Path(__file__).parent
    paths_to_try.extend([
        script_dir / "config.json",
        script_dir / "tokens.json",
        script_dir / "settings.json",
        DEFAULT_TOKEN_FILE,
    ])

    for path in paths_to_try:
        if path.exists():
            try:
                content = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(content, dict):
                    token = content.get("id_token") or content.get("idToken") or content.get("authorization")
                    if token:
                        print(f"[+] Loaded id_token from: {path.resolve()}")
                        return token
            except Exception as e:
                print(f"[-] Warning: Failed to read {path}: {e}")

    print("[-] Error: Could not find 'id_token' in any JSON config file.")
    print(f"    Searched locations: {[str(p.resolve()) for p in paths_to_try]}")
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Test endpoint update-enhance")
    parser.add_argument("--enhance-id", help="Enhance ID to update")
    parser.add_argument("--config", help="Path to JSON config file containing id_token")
    args = parser.parse_args()

    # Get enhance_id from argument or prompt input
    enhance_id = args.enhance_id
    if not enhance_id:
        try:
            enhance_id = input("Nhập enhance_id: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nĐã hủy.")
            sys.exit(0)

    if not enhance_id:
        print("[-] Error: enhance_id không được để trống.")
        sys.exit(1)

    # Load id_token from JSON config
    id_token = load_id_token(args.config)

    print("\nSending update-enhance request...")
    print(f"  - endpoint: update-enhance")
    print(f"  - enhance_id: {enhance_id}")
    print(f"  - isWatermarked: False")

    try:
        if api_post is not None:
            res = api_post("update-enhance", {"id": enhance_id, "isWatermarked": False}, id_token)
        else:
            res = api_post_standalone("update-enhance", {"id": enhance_id, "isWatermarked": False}, id_token)

        print("\n[+] Success! Server response:")
        print(json.dumps(res, indent=2, ensure_ascii=False))

    except Exception as exc:
        print_system_exception(f"service.fotello_upload_and_enhance update-enhance={enhance_id}", exc)


if __name__ == "__main__":
    main()
