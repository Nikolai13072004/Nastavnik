"""Start a fresh, bounded review stack. Never reset or remove existing data."""

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "max"


def run(args, env, capture=False):
    result = subprocess.run(args, cwd=ROOT, env=env, check=True,
                            stdout=subprocess.PIPE if capture else None, text=True)
    return result.stdout.strip() if capture else ""


def gigachat_settings(path):
    allowed = ("GIGACHAT_AUTH_KEY", "GIGACHAT_SCOPE", "GIGACHAT_MODEL")
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() in allowed:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            values[name.strip()] = value
    result = {name: values.get(name, "") for name in allowed}
    if any(not result[name] for name in allowed):
        raise SystemExit("Private GigaChat settings are incomplete.")
    if result["GIGACHAT_SCOPE"] not in ("GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP"):
        raise SystemExit("GigaChat scope is invalid.")
    if not re.fullmatch(r"[A-Za-z0-9+/=]{16,8192}", result["GIGACHAT_AUTH_KEY"]):
        raise SystemExit("GigaChat authorization key format is invalid.")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", result["GIGACHAT_MODEL"]):
        raise SystemExit("GigaChat model name is invalid.")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--prodigy-image", default="prodigy-max:tester-ready-v2-20260927")
    parser.add_argument("--vedomo-image", default="vedomo-backend:max-knowledge-20260926")
    parser.add_argument("--model-volume", default="vedomo-max-local-models")
    parser.add_argument("--download-model", action="store_true")
    parser.add_argument("--gigachat-env", type=Path)
    parser.add_argument("--gigachat-ca", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"prodigy-max-review-[a-z0-9-]{1,40}", args.project):
        raise SystemExit("Choose a separate prodigy-max-review-* project.")
    if bool(args.gigachat_env) != bool(args.gigachat_ca):
        raise SystemExit("Provide both private GigaChat settings and a trusted PEM bundle.")
    env = os.environ.copy()
    if args.download_model:
        args.model_volume = f"{args.project}_models"
    env.update({"REVIEW_PRODIGY_IMAGE": args.prodigy_image, "REVIEW_VEDOMO_IMAGE": args.vedomo_image,
                "REVIEW_MODEL_VOLUME": args.model_volume})
    if args.gigachat_env:
        if not args.gigachat_ca.is_file():
            raise SystemExit("Trusted PEM bundle is missing.")
        env.update(gigachat_settings(args.gigachat_env))
        env["REVIEW_GIGACHAT_CA"] = str(args.gigachat_ca.resolve())
    for kind in ("container", "volume", "network"):
        flags = "-aq" if kind == "container" else "-q"
        listed = run(["docker", kind, "ls", flags, "--filter", f"label=com.docker.compose.project={args.project}"], env, True)
        if listed:
            raise SystemExit("This review project already has resources. Choose a new name; nothing was removed.")
    for image in (args.prodigy_image, args.vedomo_image, "postgres:16-alpine", "nginx:stable-alpine"):
        run(["docker", "image", "inspect", image], env, True)
    if args.download_model:
        existing = run(["docker", "volume", "ls", "--format", "{{.Name}}"], env, True).splitlines()
        if args.model_volume in existing:
            raise SystemExit("The new model volume already exists; nothing was overwritten.")
        run(["docker", "volume", "create", "--label", f"com.docker.compose.project={args.project}", args.model_volume], env, True)
    else:
        run(["docker", "volume", "inspect", args.model_volume], env, True)
    model_command = ["docker", "run", "--rm", "--network", "bridge" if args.download_model else "none",
         "--memory", "256m", "--cpus", "0.25", "--pids-limit", "48",
         "-e", "HF_HUB_DISABLE_XET=1", "-e", "HF_HUB_DISABLE_TELEMETRY=1",
         "--mount", f"source={args.model_volume},target=/models" + ("" if args.download_model else ",readonly"),
         "--mount", f"type=bind,source={DEPLOY / 'prepare-review-model.py'},target=/run/prepare-model.py,readonly",
         "--entrypoint", "python", args.vedomo_image, "/run/prepare-model.py"]
    if args.download_model:
        model_command.append("--download")
    run(model_command, env)
    env_file = DEPLOY / ".env.joint-review"
    if not env_file.exists():
        run([sys.executable, str(DEPLOY / "create-review-env.py"), "--joint"], env)
    compose = ["docker", "compose", "-p", args.project, "--env-file", str(env_file),
               "-f", str(DEPLOY / "compose.review.yml"), "-f", str(DEPLOY / "compose.joint-review.yml")]
    if args.gigachat_env:
        compose += ["-f", str(DEPLOY / "compose.gigachat-review.yml")]
    run(compose + ["config", "--quiet"], env)
    try:
        run(compose + ["up", "-d", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "180"], env)
        run(compose + ["exec", "-T", "vedomo-api", "python", "/run/setup-joint-review.py"], env)
        run(compose + ["exec", "-T", "web", "node", "node_modules/tsx/dist/cli.mjs", "scripts/joint-review-smoke.ts"], env)
        if args.gigachat_env:
            run(compose + ["exec", "-T", "vedomo-api", "python", "scripts/check_gigachat.py"], env)
            run(compose + ["exec", "-T", "web", "node", "node_modules/tsx/dist/cli.mjs", "scripts/joint-review-ai-smoke.ts"], env)
        print("REVIEW_PASSED: isolated databases and model checked. Native MAX delivery is not part of this check.")
    finally:
        run(compose + ["stop", "--timeout", "30"], env)
        print(f"Only {args.project} stopped. Review volumes kept; no data removed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"REVIEW_FAILED ({type(error).__name__}); credentials were not printed.") from None
