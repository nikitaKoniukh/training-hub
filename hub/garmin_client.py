"""Garmin Connect login from token JSON (env GARMIN_TOKENS) with token rotation.

python-garminconnect >= 0.3 uses the mobile SSO / DI OAuth flow (garth is dead).
Tokens refresh themselves; after every run we write the current token JSON to
TOKENS_OUT so the workflow can store the refreshed value back into the secret.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from garminconnect import Garmin

TOKENS_OUT = Path(os.getenv("GARMIN_TOKENS_OUT", ".garmin_tokens_out.json"))


def login() -> Garmin:
    tokens = os.getenv("GARMIN_TOKENS", "").strip()
    if not tokens:
        sys.exit("GARMIN_TOKENS is empty. Run scripts/bootstrap_login.py locally first.")
    try:
        json.loads(tokens)
    except json.JSONDecodeError:
        sys.exit("GARMIN_TOKENS is not valid JSON (paste the whole garmin_tokens.json).")

    api = Garmin()
    api.login(tokenstore=tokens)
    return api


def save_tokens(api: Garmin) -> None:
    """Persist possibly-refreshed tokens so the workflow can rotate the secret."""
    try:
        TOKENS_OUT.write_text(api.client.dumps())
        TOKENS_OUT.chmod(0o600)
    except Exception as exc:  # never fail the run because of rotation
        print(f"warn: could not dump tokens: {exc}", file=sys.stderr)
