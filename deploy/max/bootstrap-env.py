"""Create fresh pilot-only secrets on the target host; never overwrite or print them."""
import os
from pathlib import Path
import secrets

target = Path("/opt/prodigy-max/.env")
if target.parent.resolve() != Path("/opt/prodigy-max"):
    raise SystemExit("Refusing a redirected deployment directory")

password = secrets.token_hex(32)
origin = "https://prodigy-max.45-139-78-17.sslip.io"
settings = {
    "MAX_PUBLIC_HOST": "prodigy-max.45-139-78-17.sslip.io",
    "POSTGRES_PASSWORD": password,
    "DATABASE_URL": f"postgresql://prodigy_max:{password}@db:5432/prodigy_max?schema=public&connection_limit=3",
    "AUTH_SECRET": secrets.token_hex(32),
    "AUTH_TRUST_HOST": "true",
    "APP_BASE_URL": origin,
    "AUTH_URL": origin,
    "NEXTAUTH_URL": origin,
    "EMAIL_PROVIDER": "stub",
    "MAX_BOT_USERNAME": "se14424319_bot",
    "MAX_BOT_TOKEN": "",
    "MAX_WEBHOOK_SECRET": secrets.token_urlsafe(32),
}
descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as output:
    output.write("\n".join(f"{key}={value}" for key, value in settings.items()) + "\n")
print("Created new pilot configuration (0600); secret values are not displayed.")
