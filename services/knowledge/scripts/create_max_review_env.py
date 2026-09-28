"""Create fresh local review credentials without overwriting an existing file."""

import os
from pathlib import Path
import secrets


def main() -> None:
    project_dir = Path(__file__).resolve().parents[1]
    target = project_dir / ".env.max-review"
    content = (
        f"REVIEW_DB_PASSWORD={secrets.token_hex(24)}\n"
        f"REVIEW_JWT_SECRET={secrets.token_hex(32)}\n"
    )
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit(".env.max-review already exists; keep its credentials.") from None
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    print("Created .env.max-review. Do not commit or share this file.")


if __name__ == "__main__":
    main()
