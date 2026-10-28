"""Validate launcher authentication before creating data or starting services."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.auth_config import get_auth_config

try:
    get_auth_config()
except RuntimeError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(2)
