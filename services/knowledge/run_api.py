"""Run the knowledge API locally."""

import os
import sys

import config


def main():
    config_errors = config.validate_config()
    if config_errors:
        print("Ошибки конфигурации:")
        for err in config_errors:
            print(f" - {err}")
        sys.exit(1)

    for path in [config.DOCS_DIR, config.DATA_DIR]:
        os.makedirs(path, exist_ok=True)

    import uvicorn

    # Host/port are env-driven so the same entrypoint serves local dev and
    # Docker. Default host stays 127.0.0.1 (loopback only) for local runs; the
    # container sets API_HOST=0.0.0.0 so the frontend container can reach it.
    host = os.getenv("API_HOST", "127.0.0.1")
    port = int(os.getenv("API_PORT", "8000"))
    print("Наставник - сервис знаний")
    print(f"URL: http://{host}:{port}")
    uvicorn.run("api_app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
