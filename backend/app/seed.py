"""Обёртка для k8s Job `seed-demo` (`python -m app.seed`, deployment.md §3.8).

Реальная логика — в app/tools/seed.py.
"""

from __future__ import annotations

import sys

from app.tools.seed import main

if __name__ == "__main__":
    sys.exit(main())
