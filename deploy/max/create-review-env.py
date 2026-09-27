"""Create local review credentials; never replace an existing configuration."""

import os
import argparse
from pathlib import Path
import secrets


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--joint", action="store_true")
    args = parser.parse_args()
    filename = ".env.joint-review" if args.joint else ".env.review"
    target = Path(__file__).resolve().parent / filename
    content = (
        f"REVIEW_DB_PASSWORD={secrets.token_hex(24)}\n"
        f"REVIEW_AUTH_SECRET={secrets.token_hex(32)}\n"
    )
    if args.joint:
        content += (
            f"REVIEW_VEDOMO_DB_PASSWORD={secrets.token_hex(24)}\n"
            f"REVIEW_JWT_SECRET={secrets.token_hex(32)}\n"
            f"REVIEW_SERVICE_TOKEN={secrets.token_urlsafe(48)}\n"
            f"REVIEW_FAKE_BOT_TOKEN=local-review-{secrets.token_hex(32)}\n"
            "REVIEW_PRODIGY_IMAGE=prodigy-max:tester-ready-v2-20260927\n"
            "REVIEW_VEDOMO_IMAGE=vedomo-backend:max-knowledge-20260926\n"
            "REVIEW_MODEL_VOLUME=vedomo-max-local-models\n"
        )
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit(f"{filename} already exists; keep its credentials.") from None
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    print(f"Created deploy/max/{filename}. Do not commit or share this file.")


if __name__ == "__main__":
    main()
