"""Check the configured GigaChat API without sending documents or printing credentials."""

import sys
import time

import config
from src.llm_engine import _GigaChatBackend


def check() -> dict:
    if config.LLM_MODE != "gigachat":
        raise ValueError("Set LLM_MODE=gigachat for this check")
    if not (config.GIGACHAT_CA_BUNDLE or config.GIGACHAT_VERIFY_SSL):
        raise ValueError("HTTPS certificate verification must be enabled")
    if not config.GIGACHAT_AUTH_KEY:
        raise ValueError("Set GIGACHAT_AUTH_KEY privately")

    backend = _GigaChatBackend()
    # A check of the selected model must not succeed through a fallback model.
    backend._models = [backend._model]
    token = backend._get_token()
    response = backend._requests.get(
        f"{backend._base_url}/models",
        headers={"Authorization": f"Bearer {token}"},
        verify=backend._verify_ssl,
        timeout=15,
    )
    response.raise_for_status()
    models = {item["id"] for item in response.json()["data"]}
    if backend._model not in models:
        raise ValueError("The selected model is not available to this key")

    started = time.monotonic()
    answer = "".join(backend.generate("Сколько будет 2 + 2? Ответь только числом.", 0, 16))
    if answer.strip().rstrip(".") != "4":
        raise ValueError("The model did not return the expected test answer")
    return {"model": backend._model, "seconds": round(time.monotonic() - started, 2)}


def main() -> int:
    try:
        result = check()
    except Exception as error:
        # Provider error bodies can contain private data; never print them here.
        print(f"GIGACHAT_CHECK_FAILED ({type(error).__name__}); review settings, TLS and API access.")
        return 1
    print(f"GIGACHAT_CHECK_OK model={result['model']} seconds={result['seconds']} TLS=verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
