#!/usr/bin/env python3
"""Main Entrypoint for hdr_exe Desktop Application.

Usage:
    python3 main.py          # Launch Desktop GUI (pywebview)
    python3 main.py --debug  # Launch with developer console and debug logs
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui.app import run_desktop


def main() -> None:
    parser = argparse.ArgumentParser(
        description="hdr_exe - HDR Photo Processing Desktop Application",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Khởi chạy với Web Inspector và debug logging",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Kiểm tra import và khởi tạo module mà không khởi chạy GUI",
    )
    args = parser.parse_args()

    if args.smoke_test:
        print("HDR Client smoke test OK")
        sys.exit(0)

    run_desktop(debug=args.debug)


if __name__ == "__main__":
    main()
