#!/usr/bin/env python3
"""One-time local login to Garmin Connect (handles MFA) -> GitHub secret.

    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    python scripts/bootstrap_login.py            # prints token JSON
    python scripts/bootstrap_login.py --set-secret nikitaKoniukh/training-hub

--set-secret needs the GitHub CLI logged in (`gh auth login`).
Credentials are used once and never stored; only the OAuth tokens are.
"""
from __future__ import annotations

import argparse
import getpass
import subprocess
import sys

from garminconnect import Garmin


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--set-secret", metavar="OWNER/REPO")
    args = p.parse_args()

    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password: ")
    api = Garmin(email=email, password=password,
                 prompt_mfa=lambda: input("MFA code (from email/app): ").strip())
    api.login()
    tokens = api.client.dumps()
    print(f"Logged in as: {api.get_full_name()}")

    if args.set_secret:
        subprocess.run(["gh", "secret", "set", "GARMIN_TOKENS", "-R", args.set_secret],
                       input=tokens.encode(), check=True)
        print(f"Secret GARMIN_TOKENS saved to {args.set_secret}")
    else:
        print("\n--- copy everything below into the GARMIN_TOKENS secret ---")
        print(tokens)
    return 0


if __name__ == "__main__":
    sys.exit(main())
