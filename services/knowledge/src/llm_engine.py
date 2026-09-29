"""
llm_engine.py - подключение к LLM (нейросети для генерации текста).
Поддерживает GigaChat, OpenRouter и локальную Ollama.
"""
import warnings
warnings.filterwarnings("ignore")

import os
import sys
import json
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


# Бэкенд для локальной работы через Ollama (без интернета)
class _OllamaBackend:

    def __init__(self):
        import requests
        # A local Ollama on the Docker host must bypass system proxies too.
        hosts = {"localhost", "127.0.0.1", urlparse(config.OLLAMA_URL).hostname}
        existing = f"{os.environ.get('NO_PROXY', '')},{os.environ.get('no_proxy', '')}"
        no_proxy = {host for host in existing.split(",") if host}
        no_proxy.update(host for host in hosts if host)
        os.environ["NO_PROXY"] = ",".join(sorted(no_proxy))
        os.environ["no_proxy"] = os.environ["NO_PROXY"]
        self._requests = requests

    def generate(self, prompt, temperature, max_tokens, stream=False):
        """Отправляет запрос к локальному серверу Ollama."""

        if "qwen3" in config.OLLAMA_MODEL.lower():
            prompt = "/no_think\n" + prompt

        estimated_prompt_tokens = max(1, len(prompt) // 3)
        num_ctx = min(
            config.LLM_CONTEXT_SIZE,
            max(4096, estimated_prompt_tokens + max_tokens + 1024),
        )

        payload = {
            "model": config.OLLAMA_MODEL,
            "prompt": prompt,
            "stream": stream,
            "options": {
                "temperature": temperature,
                "top_p": config.LLM_TOP_P,
                "num_predict": max_tokens,
                "num_ctx": num_ctx,
                "repeat_penalty": config.LLM_REPEAT_PENALTY,
                "stop": ["<|im_end|>", "</s>"],
            },
        }

        if stream:
            r = self._requests.post(config.OLLAMA_URL, json=payload, stream=True, timeout=180)
            r.raise_for_status()

            for line in r.iter_lines():
                if line:
                    chunk = json.loads(line)
                    yield chunk.get("response", "")
                    if chunk.get("done"):
                        break
        else:
            r = self._requests.post(config.OLLAMA_URL, json=payload, timeout=180)
            r.raise_for_status()

            data = r.json()
            text = (data.get("response") or "").strip()

            yield text

    def is_available(self):
        """Проверяет, запущен ли сервер Ollama."""
        try:
            tags_url = config.OLLAMA_URL.rsplit("/", 1)[0] + "/tags"
            r = self._requests.get(tags_url, timeout=3)
            return r.status_code == 200
        except Exception:
            return False


# Бэкенд для облачной работы через API (OpenRouter)
class _ApiBackend:

    def __init__(self):
        import requests
        self._requests = requests
        self._api_url = getattr(config, "API_URL", "https://openrouter.ai/api/v1/chat/completions")
        self._api_key = getattr(config, "API_KEY", "")
        self._api_model = getattr(config, "API_MODEL", "qwen/qwen3-32b")

        # Отключаем прокси для API-серверов
        os.environ["NO_PROXY"] = os.environ.get("NO_PROXY", "") + ",openrouter.ai"
        os.environ["no_proxy"] = os.environ.get("no_proxy", "") + ",openrouter.ai"

        # Проверяем наличие ключа
        if not self._api_key:
            self._api_key = os.environ.get("API_KEY", "")
        if not self._api_key:
            print("API_KEY не задан! Установите в config.py")

    def generate(self, prompt, temperature, max_tokens, stream=False):
        """Отправляет запрос к OpenRouter API."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        # Разделяем промпт на системное сообщение и вопрос пользователя
        system_prompt = getattr(config, "SYSTEM_PROMPT", "")
        if system_prompt and prompt.startswith(system_prompt):
            user_content = prompt[len(system_prompt):].strip()
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]
        else:
            messages = [
                {"role": "system", "content": "Ты - полезный ассистент. Отвечай на русском языке."},
                {"role": "user", "content": prompt},
            ]

        # Для Qwen3: отключаем режим размышлений (ускоряет ответ)
        if "qwen3" in self._api_model.lower():
            messages[-1]["content"] = "/no_think\n" + messages[-1]["content"]

        payload = {
            "model": self._api_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }

        if stream:
            # Потоковый режим: ответ приходит по кусочкам (токенам)
            r = self._requests.post(self._api_url, headers=headers, json=payload, stream=True, timeout=120)
            if r.status_code != 200:
                raise RuntimeError(f"API ошибка {r.status_code}: {r.text[:500]}")
            for line in r.iter_lines():
                if not line:
                    continue
                line_str = line.decode("utf-8")
                if line_str.startswith("data: "):
                    data_str = line_str[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        content = chunk["choices"][0].get("delta", {}).get("content") or ""
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
        else:
            # Обычный режим: ждём полный ответ
            r = self._requests.post(self._api_url, headers=headers, json=payload, timeout=120)
            if r.status_code != 200:
                raise RuntimeError(f"API ошибка {r.status_code}: {r.text[:500]}")
            data = r.json()
            text = (data["choices"][0]["message"].get("content") or "").strip()
            if text:
                yield text

    def is_available(self):
        """Проверяет, доступен ли API."""
        if not self._api_key:
            return False
        try:
            headers = {"Authorization": f"Bearer {self._api_key}"}
            r = self._requests.get(
                self._api_url.replace("/chat/completions", "/models"),
                headers=headers, timeout=5
            )
            return r.status_code == 200
        except Exception:
            return False


# Бэкенд для российской модели GigaChat (Сбер).
# OAuth: Basic auth key → короткоживущий access_token (TTL ~30 мин), кэшируется.
# Чат: v1 endpoint OpenAI-совместим, стандартный SSE.
# Сертификат Минцифры для ngw.devices.sberbank.ru нужен для verify=True; по
# умолчанию SSL-verify выключен, чтобы можно было протестировать сразу без
# установки корневого сертификата в систему (см. GIGACHAT_VERIFY_SSL в config).
class _GigaChatModelUnavailable(RuntimeError):
    """Модель отсутствует у ключа (404 "No such model") — можно взять запасную."""


class _GigaChatBackend:

    _OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    def __init__(self):
        import requests
        import uuid
        self._requests = requests
        self._uuid = uuid

        self._auth_key = getattr(config, "GIGACHAT_AUTH_KEY", "") or os.environ.get("GIGACHAT_AUTH_KEY", "")
        self._scope = getattr(config, "GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
        self._model = getattr(config, "GIGACHAT_MODEL", "GigaChat")
        self._base_url = config.GIGACHAT_BASE_URL.strip().rstrip("/")
        endpoint = urlparse(self._base_url)
        if (endpoint.scheme != "https" or not endpoint.hostname
                or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
            raise ValueError("GIGACHAT_BASE_URL must be an HTTPS API URL without credentials")
        self._CHAT_URL = f"{self._base_url}/chat/completions"
        self._verify_ssl = config.GIGACHAT_CA_BUNDLE or config.GIGACHAT_VERIFY_SSL

        # Цепочка "основная + запасные", без дублей и с сохранением порядка.
        self._models = []
        for name in [self._model] + list(getattr(config, "GIGACHAT_MODEL_FALLBACKS", [])):
            if name and name not in self._models:
                self._models.append(name)
        # Модель -> unix-время, до которого её не трогаем после 404.
        self._model_cooldown = {}
        self._retry_seconds = getattr(config, "GIGACHAT_MODEL_RETRY_SECONDS", 600)

        # Кэш токена
        self._token = None
        self._token_expires_at = 0  # unix timestamp (сек)

        # Подавляем InsecureRequestWarning, если verify выключен намеренно
        if not self._verify_ssl:
            try:
                from urllib3.exceptions import InsecureRequestWarning
                self._requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
            except Exception:
                pass

        if not self._auth_key:
            print("GIGACHAT_AUTH_KEY не задан! Установите в .env (см. developers.sber.ru).")

    def _get_token(self, force_refresh=False):
        """Возвращает access_token, обновляя его по OAuth при необходимости."""
        import time
        # Обновляем за 30 секунд до реального истечения, чтобы не поймать 401 в стриме
        if not force_refresh and self._token and time.time() < (self._token_expires_at - 30):
            return self._token

        if not self._auth_key:
            raise RuntimeError("GIGACHAT_AUTH_KEY не задан.")

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": str(self._uuid.uuid4()),
            "Authorization": f"Basic {self._auth_key}",
        }
        data = {"scope": self._scope}

        r = self._requests.post(
            self._OAUTH_URL, headers=headers, data=data,
            timeout=15, verify=self._verify_ssl,
        )
        if r.status_code != 200:
            raise RuntimeError(f"GigaChat OAuth ошибка {r.status_code}: {r.text[:300]}")

        payload = r.json()
        self._token = payload["access_token"]
        # expires_at приходит в миллисекундах
        self._token_expires_at = payload["expires_at"] / 1000.0
        return self._token

    def _build_messages(self, prompt):
        """Собирает messages из промпта, отделяя SYSTEM_PROMPT если он в начале."""
        system_prompt = getattr(config, "SYSTEM_PROMPT", "")
        if system_prompt and prompt.startswith(system_prompt):
            user_content = prompt[len(system_prompt):].strip()
            return [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]
        return [
            {"role": "system", "content": "Ты - полезный ассистент. Отвечай на русском языке."},
            {"role": "user", "content": prompt},
        ]

    def _do_request(self, payload, stream, _rate_limit_waits=None):
        """Один HTTP-вызов /chat/completions с автообновлением токена по 401.

        Сетевой сбой (обрыв TLS, таймаут соединения) повторяется один раз: он,
        в отличие от 404, не означает, что запрос плохой, — а без повтора
        единичный блип обрывал бы ответ на глазах у пользователя.
        """
        import time

        # 429 отдаётся, когда упёрлись в лимит запросов ключа. Это не отказ, а
        # «подожди»: пауза с удвоением почти всегда снимает вопрос. Без неё
        # всплеск активности (несколько студентов разом, прогон eval) роняет
        # ответ прямо пользователю - проверено 03.09.2026, два параллельных
        # прогона по freemium-ключу оборвали работу на середине.
        # Список передаётся при повторе, иначе рекурсия создавала бы его заново
        # и повторяла бы бесконечно.
        rate_limit_waits = [2, 6, 15] if _rate_limit_waits is None else _rate_limit_waits

        for attempt in (1, 2):
            token = self._get_token(force_refresh=(attempt == 2))
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
            }
            try:
                r = self._requests.post(
                    self._CHAT_URL, headers=headers, json=payload,
                    stream=stream, timeout=180, verify=self._verify_ssl,
                )
            except (self._requests.exceptions.SSLError,
                    self._requests.exceptions.ConnectionError,
                    self._requests.exceptions.Timeout) as exc:
                if attempt == 1:
                    time.sleep(1)
                    continue
                raise RuntimeError(
                    f"GigaChat: не удалось соединиться ({type(exc).__name__}). "
                    "Проверьте сеть, сертификаты и GIGACHAT_BASE_URL."
                ) from exc
            if r.status_code == 429 and rate_limit_waits:
                # Лимит запросов: ждём и пробуем снова тем же токеном. Счётчик
                # attempt не трогаем - это не проблема авторизации.
                wait = rate_limit_waits.pop(0)
                print(f"GigaChat: лимит запросов, жду {wait} с и повторяю")
                time.sleep(wait)
                return self._do_request(payload, stream, rate_limit_waits)
            if r.status_code == 401 and attempt == 1:
                # Токен протух между кэшем и вызовом — обновим и повторим один раз
                continue
            if r.status_code == 404 and "no such model" in r.text.lower():
                # Не ошибка запроса, а отсутствующая у ключа модель — вызывающий
                # код возьмёт следующую из цепочки.
                raise _GigaChatModelUnavailable(payload.get("model", ""))
            if r.status_code != 200:
                raise RuntimeError(f"GigaChat API ошибка {r.status_code}: {r.text[:500]}")
            return r
        raise RuntimeError("GigaChat: не удалось получить валидный токен после повтора")

    def _request_any_model(self, payload, stream):
        """``_do_request``, перебирая цепочку моделей на 404 "No such model".

        Модель, ответившая 404, уходит в «карантин» на
        ``GIGACHAT_MODEL_RETRY_SECONDS`` — не навсегда: доступ к моделям на
        freemium-ключе *мигает* (01.09.2026 GigaChat-3-Ultra отвечала, пропадала
        и возвращалась в течение часа). Выкидывать её насовсем значило бы, что
        один 404 на старте до перезапуска процесса держит сервис на запасной
        модели, хотя основная давно ожила. Поэтому цепочка всегда просматривается
        с начала: как только карантин истёк, снова пробуем предпочтительную.
        """
        import time

        last_error = None
        now = time.time()
        for name in self._models:
            if self._model_cooldown.get(name, 0) > now:
                continue  # ещё в карантине — не тратим на неё запрос
            payload["model"] = name
            try:
                r = self._do_request(payload, stream=stream)
            except _GigaChatModelUnavailable as exc:
                last_error = exc
                self._model_cooldown[name] = now + self._retry_seconds
                print(f"GigaChat: модель {name} недоступна, пробую следующую")
                continue
            if name != self._model:
                print(f"GigaChat: переключился на модель {name}")
                self._model = name
            return r

        if last_error is None:
            # Все модели в карантине — снимаем его и даём цепочке ещё один шанс,
            # иначе запрос упал бы, ни разу никуда не сходив.
            self._model_cooldown.clear()
            return self._request_any_model(payload, stream)

        raise RuntimeError(
            "GigaChat: ни одна из моделей недоступна "
            f"(последняя ошибка: {last_error}). Проверьте GIGACHAT_MODEL "
            "и GIGACHAT_MODEL_FALLBACKS."
        )

    def generate(self, prompt, temperature, max_tokens, stream=False):
        """Отправляет запрос к GigaChat API (v1, OpenAI-совместимый)."""
        payload = {
            "model": self._model,
            "messages": self._build_messages(prompt),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }

        if stream:
            r = self._request_any_model(payload, stream=True)
            for line in r.iter_lines():
                if not line:
                    continue
                line_str = line.decode("utf-8")
                if line_str.startswith("data: "):
                    data_str = line_str[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        content = chunk["choices"][0].get("delta", {}).get("content") or ""
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
        else:
            r = self._request_any_model(payload, stream=False)
            data = r.json()
            text = (data["choices"][0]["message"].get("content") or "").strip()
            if text:
                yield text

    def is_available(self):
        """Проверяет, что ключ задан и OAuth-эндпоинт отдаёт токен."""
        if not self._auth_key:
            return False
        try:
            self._get_token()
            return True
        except Exception:
            return False


VISION_PROMPT = (
    "Распознай и выпиши ВЕСЬ текст с изображения как есть, сохраняя структуру "
    "(заголовки, списки, абзацы). Математические формулы записывай в LaTeX "
    "($...$). Схемы и рисунки кратко опиши словами в [квадратных скобках]. "
    "Особенно внимательно сохраняй дроби, знаки и диапазоны: ¼, ½ и ¾ - это "
    "дроби, их нельзя превращать в 14, 12, 34 или диапазон 3-4. Не заменяй "
    "¾-1 на 3-4. Сверяй число с дублирующим значением в скобках, если оно есть; "
    "если символ не читается однозначно, напиши [неразборчиво], а не угадывай. "
    "Не добавляй ничего от себя и не комментируй - только содержимое."
)


def transcribe_image(image_bytes: bytes, mime_type: str = "image/png") -> str:
    """One-shot image → text (Stage 56).

    Turns a photo of a whiteboard / handwritten notes into indexable text,
    handling formulas and diagrams that plain OCR mangles. Routed by
    ``LLM_MODE``: GigaChat keeps the whole pipeline inside the Russian stack,
    OpenRouter is the original path. Ollama has no vision model configured, so
    it raises rather than silently producing nothing.
    """
    if config.LLM_MODE == "gigachat":
        return _transcribe_via_gigachat(image_bytes, mime_type)
    if config.LLM_MODE == "api":
        return _transcribe_via_openrouter(image_bytes, mime_type)
    raise RuntimeError(
        "Распознавание фото недоступно в режиме ollama - "
        "переключите LLM_MODE на gigachat или api."
    )


def _transcribe_via_gigachat(image_bytes: bytes, mime_type: str = "image/png") -> str:
    """Vision через GigaChat: файл загружается, затем прикрепляется к сообщению.

    В отличие от OpenRouter картинка не кодируется в base64 внутрь запроса —
    GigaChat принимает её отдельной загрузкой и возвращает ``id``, который потом
    указывается в ``attachments``. Загруженный файл удаляем сразу после ответа:
    иначе каждое распознанное фото навсегда оседает в аккаунте пользователя.
    """
    import requests

    backend = _GigaChatBackend()
    if not backend._auth_key:
        raise RuntimeError("GIGACHAT_AUTH_KEY не задан.")
    token = backend._get_token()
    verify = backend._verify_ssl
    base = backend._base_url
    auth = {"Authorization": f"Bearer {token}"}

    upload = requests.post(
        f"{base}/files",
        headers=auth,
        files={"file": ("image.png", image_bytes, mime_type or "image/png")},
        data={"purpose": "general"},
        verify=verify,
        timeout=120,
    )
    if upload.status_code not in (200, 201):
        raise RuntimeError(f"GigaChat: загрузка фото не удалась ({upload.status_code}): {upload.text[:300]}")
    file_id = upload.json().get("id")
    if not file_id:
        raise RuntimeError("GigaChat: загрузка фото не вернула id файла.")

    try:
        resp = requests.post(
            f"{base}/chat/completions",
            headers={**auth, "Content-Type": "application/json"},
            json={
                "model": getattr(config, "GIGACHAT_VISION_MODEL", "GigaChat-2-Max"),
                "messages": [{"role": "user", "content": VISION_PROMPT, "attachments": [file_id]}],
                "max_tokens": getattr(config, "VISION_MAX_TOKENS", 4000),
                "temperature": 0,
            },
            verify=verify,
            timeout=180,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"GigaChat Vision ошибка {resp.status_code}: {resp.text[:300]}")
        return (resp.json()["choices"][0]["message"].get("content") or "").strip()
    finally:
        # Уборка не должна маскировать ошибку распознавания выше.
        try:
            requests.post(f"{base}/files/{file_id}/delete", headers=auth, verify=verify, timeout=30)
        except Exception:
            pass


def _transcribe_via_openrouter(image_bytes: bytes, mime_type: str = "image/png") -> str:
    """Vision через OpenRouter (исходный путь Stage 56)."""
    import base64

    import requests

    api_key = getattr(config, "API_KEY", "") or os.environ.get("API_KEY", "")
    if not api_key:
        raise RuntimeError("API_KEY не задан.")

    api_url = getattr(config, "API_URL", "https://openrouter.ai/api/v1/chat/completions")
    model = getattr(config, "VISION_MODEL", "") or "google/gemini-2.0-flash-001"
    data_url = f"data:{mime_type or 'image/png'};base64,{base64.b64encode(image_bytes).decode('ascii')}"

    prompt = (
        "Распознай и выпиши ВЕСЬ текст с изображения как есть, сохраняя структуру "
        "(заголовки, списки, абзацы). Математические формулы записывай в LaTeX "
        "($...$). Схемы и рисунки кратко опиши словами в [квадратных скобках]. "
        "Не добавляй ничего от себя и не комментируй - только содержимое."
    )
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "max_tokens": getattr(config, "VISION_MAX_TOKENS", 4000),
        "temperature": 0,
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    resp = requests.post(api_url, headers=headers, json=payload, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"Vision API ошибка {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    return (data["choices"][0]["message"].get("content") or "").strip()


# Единый интерфейс для работы с LLM
class LLMEngine:
    """
    Выбирает нужный бэкенд (API или Ollama) и предоставляет
    единые методы call() и stream() для всего приложения.
    """

    def __init__(self):
        mode = config.LLM_MODE
        if mode == "ollama":
            self._backend = _OllamaBackend()
        elif mode == "gigachat":
            self._backend = _GigaChatBackend()
        else:
            self._backend = _ApiBackend()

    def call(self, prompt, temperature=None, max_tokens=None):
        """Отправить запрос и получить полный ответ (строкой)."""
        temp = temperature if temperature is not None else config.LLM_TEMPERATURE
        tokens = max_tokens if max_tokens is not None else config.LLM_MAX_TOKENS
        result = ""
        for token in self._backend.generate(prompt, temp, tokens, stream=False):
            result += token
        return result.strip()

    def stream(self, prompt, temperature=None, max_tokens=None):
        """Отправить запрос и получать ответ по токенам (для стриминга в чате)."""
        temp = temperature if temperature is not None else config.LLM_TEMPERATURE
        tokens = max_tokens if max_tokens is not None else config.LLM_MAX_TOKENS
        yield from self._backend.generate(prompt, temp, tokens, stream=True)

    def generate_with_context(self, template, topic, context, stream=False):
        """Подставить контекст в шаблон и сгенерировать ответ"""
        prompt = template.format(system=config.SYSTEM_PROMPT, topic=topic, context=context)
        if stream:
            return self.stream(prompt)
        return self.call(prompt)

    def is_available(self):
        """Проверить, доступен ли выбранный бэкенд"""
        return self._backend.is_available()
