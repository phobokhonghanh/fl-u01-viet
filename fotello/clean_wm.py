#!/usr/bin/env python3
"""Root executable CLI entrypoint for watermark cleaner."""

import sys
from backend.watermark_cleaner.cli import main

if __name__ == "__main__":
    sys.exit(main())
