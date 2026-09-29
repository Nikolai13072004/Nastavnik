"""
config.py - все настройки системы в одном месте.
Меняя этот файл, можно переключать режимы работы без изменения кода.
"""
import os
from dotenv import load_dotenv
from src.prompts import SYSTEM_PROMPT, PROMPTS

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRODIGY_DOCUMENT_IMPORT_ENABLED = os.getenv("PRODIGY_DOCUMENT_IMPORT_ENABLED", "false").lower() == "true"

# Папки проекта
DOCS_DIR   = os.path.join(BASE_DIR, "docs")
DATA_DIR   = os.path.join(BASE_DIR, "data")
CHROMA_DIR = os.path.join(DATA_DIR, "chromadb")

# Режим работы LLM: "api" (OpenRouter/облако), "ollama" (локально) или
# "gigachat" (российский Сбер — для клиентов с требованием отечественной LLM).
LLM_MODE = os.getenv("LLM_MODE", "api")

# Настройки API (OpenRouter)
API_URL   = "https://openrouter.ai/api/v1/chat/completions"
API_KEY  = os.getenv("API_KEY", "")
API_MODEL = os.getenv("API_MODEL", "qwen/qwen3-32b")

# --- GigaChat (Сбер, российская LLM) --------------------------------------
# Ключ авторизации ("Authorization key" из личного кабинета на
# developers.sber.ru — уже base64-encoded строка client_id:client_secret).
GIGACHAT_AUTH_KEY = os.getenv("GIGACHAT_AUTH_KEY", "")
GIGACHAT_BASE_URL = os.getenv("GIGACHAT_BASE_URL", "https://api.giga.chat/v1")
GIGACHAT_CA_BUNDLE = os.getenv("GIGACHAT_CA_BUNDLE", "")
# Scope должен соответствовать ключу: GIGACHAT_API_PERS, GIGACHAT_API_B2B или GIGACHAT_API_CORP.
GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
# Доступность модели и тариф проверяются отдельно для используемого ключа.
# Проверять доступность своим ключом: GET /api/v1/models. Несуществующая модель
# здесь не ошибка, а тихая потеря качества - см. GIGACHAT_MODEL_FALLBACKS ниже.
GIGACHAT_MODEL = os.getenv("GIGACHAT_MODEL", "GigaChat-2-Pro")
# Запасные модели на случай, если основная отдаёт 404 "No such model".
# Состав моделей на freemium-ключе меняется без предупреждения (проверено
# 01.09.2026: GigaChat-3-Ultra исчезла за час, GigaChat-Max отвалилась посреди
# прогона), поэтому одна недоступная модель не должна ронять чат целиком.
# Пустая строка отключает фолбэк — тогда 404 поднимается как ошибка.
#
# ПОРЯДОК ЗДЕСЬ - ЭТО КАЧЕСТВО ОТВЕТОВ, а не просто список: берётся первая
# доступная модель. Проверять доступность своим ключом (GET /api/v1/models):
# несуществующая модель в GIGACHAT_MODEL не ошибка, а тихий съезд на следующую
# строку списка.
#
# Порядок выбран замером 04.09.2026 на четырёх наборах-ловушках (74 вопроса:
# ТК РФ, ПДД, инструкции к препаратам, СП 60.13330.2020), одна и та же
# метрика, один и тот же контекст:
#
#     GigaChat-2-Pro  63/74      GigaChat-2-Max  53/74
#
# Часть разрыва - артефакт разметки (ключевые слова писались под манеру Pro:
# Max отвечает «3 миллилитра» там, где ждали «3 мл»). Но восемь провалов Max
# к разметке не сводятся и прочитаны глазами: это ОТКАЗЫ на вопросы, ответ на
# которые в документе есть и Pro его даёт - «обгон запрещён ближе чем за 100
# метров», «перевозка детей до 12 лет на заднем сиденье мотоцикла запрещена».
# Max систематически осторожнее, и для ассистента по документам это хуже:
# отказ на найденный ответ обесценивает инструмент.
#
# Max выигрывает узко и там, где нужен счёт по позиции в таблице: СП 60,
# таблица Ж.3, коэффициент Kв при x/l=80 (верно 2,4, соседняя колонка 2,6).
# Замер на восьми прогонах одного вопроса: Pro ошибается 7 раз из 8. Это не
# «всегда» - первая проверка на четырёх прогонах дала 4 из 4 и создала ложное
# впечатление детерминированности; ошибка устойчивая, но вероятностная, и
# один удачный прогон ничего не опровергает. Если рабочая нагрузка -
# преимущественно числовые таблицы, порядок стоит пересмотреть замером на
# своих документах, а не менять на глаз.
GIGACHAT_MODEL_FALLBACKS = [
    m.strip()
    for m in os.getenv(
        "GIGACHAT_MODEL_FALLBACKS", "GigaChat-2-Pro,GigaChat-2-Max,GigaChat-Max,GigaChat"
    ).split(",")
    if m.strip()
]
# Сколько секунд не трогать модель после её 404. Доступ мигает, поэтому
# «карантин» временный: как только он истёк, снова пробуем предпочтительную
# модель, а не сидим на запасной до перезапуска процесса.
GIGACHAT_MODEL_RETRY_SECONDS = int(os.getenv("GIGACHAT_MODEL_RETRY_SECONDS", "600"))
# Модель для распознавания фото при LLM_MODE=gigachat. Берём старшую из
# доступных: на проверке 01.09.2026 и Max, и Pro читали русский текст и
# собирали формулу в LaTeX верно, но мелкие детали (нижний индекс `s0`, буква
# параметра) плывут от запуска к запуску у обеих — так что это не «точная
# распознавалка», а помощник, чей результат пользователь видит перед загрузкой.
GIGACHAT_VISION_MODEL = os.getenv("GIGACHAT_VISION_MODEL", "GigaChat-2-Max")
# Проверка HTTPS включена по умолчанию. Дополнительные доверенные сертификаты
# задаются через GIGACHAT_CA_BUNDLE, без изменения системного хранилища.
GIGACHAT_VERIFY_SSL = os.getenv("GIGACHAT_VERIFY_SSL", "true").lower() in ("1", "true", "yes", "on")
# Vision model for photo → text (Stage 56). Any multimodal model on OpenRouter;
# default is cheap + good at handwriting/formulas. Override via .env if needed.
VISION_MODEL = os.getenv("VISION_MODEL", "google/gemini-2.0-flash-001")
VISION_MAX_TOKENS = int(os.getenv("VISION_MAX_TOKENS", "4000"))
# Reject images larger than this before the (paid) vision call (Stage 56).
VISION_MAX_IMAGE_BYTES = int(os.getenv("VISION_MAX_IMAGE_BYTES", str(12 * 1024 * 1024)))

# Настройки Ollama (локальный режим)
OLLAMA_URL   = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")

# Параметры генерации
LLM_MAX_TOKENS     = 2048
LLM_TEMPERATURE    = 0.1
LLM_TOP_P         = 0.9
LLM_REPEAT_PENALTY = 1.15
LLM_CONTEXT_SIZE   = 32768

# Эмбеддинги. Имя берётся из env, чтобы можно было указать ЛОКАЛЬНЫЙ путь к
# модели (офлайн-деплой / сети, где HuggingFace недоступен - Stage 46), напр.
# EMBEDDING_MODEL=/models/bge-m3. По умолчанию - имя на HF Hub.
EMBEDDING_MODEL  = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "cpu").strip() or "cpu"

# Реранкер (тоже можно указать локальный путь через env).
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
RERANKER_DEVICE = os.getenv("RERANKER_DEVICE", EMBEDDING_DEVICE).strip() or "cpu"
# CPU latency guard validated on the three 16.09 blockers: 256 kept all final
# top-12 passages when combined with dense rescue; 192 dropped engineering-14.
RERANKER_MAX_LENGTH = int(os.getenv("RERANKER_MAX_LENGTH", "256"))
# Реранкер можно выключить через env (USE_RERANKER=false) - тогда поиск работает
# в лексическом запасном режиме. Полезно, когда веса реранкера недоступны и не
# хочется, чтобы первый поиск завис на попытке их скачать (Stage 46).
USE_RERANKER   = os.getenv("USE_RERANKER", "true").lower() in ("1", "true", "yes", "on")

# ChromaDB
COLLECTION_NAME = "textbot_docs"

# Чанкинг
CHUNK_SIZE    = 1200
CHUNK_OVERLAP = 200

# Индексация
INDEX_BATCH_SIZE = 64

# Поиск. 80 — консервативный профиль качества. Уменьшать число кандидатов
# можно только после одинакового retrieval-eval на материалах пилота: локальный
# замер 15.09.2026 показал большой выигрыш CPU при 20, но это ещё не универсальная
# гарантия для чужих документов.
RETRIEVAL_TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "80"))
MIN_RELEVANCE   = 0.00
MAX_CTX_CHARS   = 18000
RERANK_TOP_K    = 12
SUMMARY_TOP_K   = 40

# Плановый тематический конспект
PLANNED_SUMMARY_ENABLED = True
PLANNED_SUMMARY_QUERIES = 7
PLANNED_SUMMARY_CHUNKS_PER_QUERY = 5
PLANNED_SUMMARY_MAX_CHUNKS = 40
PLANNED_SUMMARY_MAX_CHUNKS_PER_SECTION = 4

# Полнофайловый конспект (без темы): потолок фрагментов для map-reduce. На
# большом материале полный обход = десятки последовательных вызовов LLM
# (зависание / обрыв соединения). Сверх потолка берём первые N фрагментов и
# честно помечаем конспект как «по началу материала». 0 = без потолка.
FULL_FILE_SUMMARY_MAX_CHUNKS = 60

# Сколько map-вызовов конспекта гнать параллельно. Промежуточные выжимки
# независимы, поэтому их можно слать одновременно (десятки секунд -> единицы).
# Держим скромно, чтобы не упереться в rate-limit провайдера. 1 = последовательно.
SUMMARY_MAP_CONCURRENCY = int(os.getenv("SUMMARY_MAP_CONCURRENCY", "4"))

# HyDE
USE_HYDE      = False
HYDE_VARIANTS = 3

# Форматы файлов. Формат определяется по СОДЕРЖИМОМУ (магические байты) внутри
# src.document_loader.detect_format, а этот список - allow-list расширений для
# загрузки. Скан-PDF/картинки/.djvu идут в OCR, старый .doc/.ppt - в конвертацию
# через LibreOffice; всё это требует системный бинарь на сервере и аккуратно
# деградирует понятной ошибкой, если его нет (Stage 40).
SUPPORTED_FORMATS = [
    # Текст и разметка
    ".pdf", ".txt", ".md", ".csv", ".rtf", ".html", ".htm",
    # Office (современный OOXML + OpenDocument)
    ".docx", ".pptx", ".xlsx", ".odt", ".odp", ".ods",
    # Office (старые бинарные - через LibreOffice / xlrd)
    ".doc", ".ppt", ".xls",
    # Книги
    ".epub", ".fb2", ".fb2.zip",
    # Сканы и картинки (OCR)
    ".djvu", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp",
]

# Лимит загрузки (байт). По умолчанию 50 МБ.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# Защита от «бомб распаковки» (Stage 41): ZIP-форматы (docx/xlsx/pptx/odt/epub)
# при 50 МБ на диске могут развернуться в гигабайты в памяти. Если суммарный
# распакованный размер превышает потолок - отказываемся парсить.
MAX_ZIP_UNCOMPRESSED_BYTES = 400 * 1024 * 1024  # 400 МБ
# Потолок извлечённого текста на один материал (Stage 41): сверх него режем,
# чтобы один файл не породил десятки тысяч чанков и не выжрал память при
# индексации. ~20 МБ текста с запасом покрывают большие учебники.
MAX_INDEX_CHARS = 20 * 1024 * 1024

# Веб-интерфейс
GUI_PORT  = 7860
GUI_SHARE = False

# --- Multi-user foundation (Stage 1: db + auth) ---------------------------
#
# Database URL is read from env so dev/CI can stay on SQLite while production
# moves to Postgres without code changes. Tests provide their own URL via the
# DATABASE_URL env var (see tests/conftest.py).
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{os.path.join(DATA_DIR, 'app.db')}")

# JWT secret. Production MUST set JWT_SECRET_KEY in the environment; the dev
# fallback below is only acceptable for local development and tests. The
# fallback is intentionally obviously-non-secret so accidentally shipping it to
# production is easy to notice in a security scan. A production-like start with
# this exact value is a hard error (see ``validate_config`` /
# ``assert_jwt_secret_safe``) - otherwise anyone could forge session tokens.
INSECURE_JWT_DEFAULT = "dev-only-insecure-jwt-secret-change-me-in-production"
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", INSECURE_JWT_DEFAULT)
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
# Access-token lifetime. Default: 7 days (60 * 24 * 7).
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", str(60 * 24 * 7)))

# Cookie carrying the access token. HttpOnly + SameSite=Lax by default.
AUTH_COOKIE_NAME = "vedomo_auth"
# Set to True only when serving the frontend over HTTPS in production.
AUTH_COOKIE_SECURE = os.getenv("AUTH_COOKIE_SECURE", "false").lower() == "true"

# Protected "root" admin (Stage 13). The user whose email matches this can
# never be demoted or banned through the admin API — not even by another
# superuser. Set it to your own login email. Empty (default) = no protected
# root. It does not auto-grant superuser; promote the account once as usual.
ROOT_ADMIN_EMAIL = os.getenv("ROOT_ADMIN_EMAIL", "").strip().lower()

# --- Rate limiting (Stage 9a) ---------------------------------------------
# Per-IP limits applied via slowapi. Tunable through env; strict on auth,
# moderate on chat/upload. Disable entirely with RATE_LIMIT_ENABLED=false
# (used by the test suite).
RATE_LIMIT_LOGIN = os.getenv("RATE_LIMIT_LOGIN", "10/minute")
RATE_LIMIT_REGISTER = os.getenv("RATE_LIMIT_REGISTER", "5/minute")
RATE_LIMIT_CHAT = os.getenv("RATE_LIMIT_CHAT", "30/minute")
RATE_LIMIT_UPLOAD = os.getenv("RATE_LIMIT_UPLOAD", "20/minute")
# Rate-limit storage backend (Phase A-prep). Default in-memory is correct for a
# single backend process; point at Redis (e.g. "redis://host:6379") to share
# counters across multiple instances when scaling horizontally.
RATE_LIMIT_STORAGE_URI = os.getenv("RATE_LIMIT_STORAGE_URI", "memory://")

# Trust a reverse proxy for the real client IP (Stage 46). Behind the prod chain
# (Caddy -> Next.js -> backend) the immediate peer is always the frontend
# container, so per-IP rate limits (login/register) and audit-log IPs would all
# collapse to one address. When true, ``src.rate_limit.client_ip`` reads the
# ``X-Real-IP`` header that Caddy sets (Caddy overwrites any client-supplied
# value with the true remote host, and the backend isn't directly reachable, so
# it can't be spoofed). Off by default - a directly-exposed backend must not
# trust a client-supplied header. The prod compose overlay sets it to true.
TRUST_PROXY_IP = os.getenv("TRUST_PROXY_IP", "false").lower() in ("1", "true", "yes", "on")


# --- Plans & quotas (Stage 12) --------------------------------------------
# Per-plan usage limits, enforced via ``src.quota``. All numbers are
# env-overridable so they can be tuned without a migration. Disable enforcement
# entirely with QUOTAS_ENABLED=false (the test suite does this; quota-specific
# tests flip it back on for their scope).
#
# ``max_materials`` is a *total* cap (current Document count); ``chat_per_day``
# / ``summary_per_day`` are rolling per-UTC-day caps. ``model`` is a forward
# hook for a cheaper free-tier model — both plans point at the configured model
# for now.
QUOTAS_ENABLED = os.getenv("QUOTAS_ENABLED", "true").lower() in ("1", "true", "yes", "on")

_DEFAULT_MODEL = API_MODEL if LLM_MODE == "api" else OLLAMA_MODEL


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


PLAN_LIMITS = {
    "free": {
        "max_materials": _int_env("PLAN_FREE_MAX_MATERIALS", 3),
        "chat_per_day": _int_env("PLAN_FREE_CHAT_PER_DAY", 15),
        "summary_per_day": _int_env("PLAN_FREE_SUMMARY_PER_DAY", 3),
        "study_per_day": _int_env("PLAN_FREE_STUDY_PER_DAY", 5),
        "model": os.getenv("PLAN_FREE_MODEL", _DEFAULT_MODEL),
    },
    "pro": {
        "max_materials": _int_env("PLAN_PRO_MAX_MATERIALS", 50),
        "chat_per_day": _int_env("PLAN_PRO_CHAT_PER_DAY", 200),
        "summary_per_day": _int_env("PLAN_PRO_SUMMARY_PER_DAY", 50),
        "study_per_day": _int_env("PLAN_PRO_STUDY_PER_DAY", 50),
        "model": os.getenv("PLAN_PRO_MODEL", _DEFAULT_MODEL),
    },
}

# Тренажёр (флешкарты/тесты, Stage 17): сколько элементов просим у LLM и сколько
# фрагментов материала берём в контекст. Одна ограниченная LLM-генерация.
STUDY_DEFAULT_COUNT = _int_env("STUDY_DEFAULT_COUNT", 10)
STUDY_MAX_COUNT = _int_env("STUDY_MAX_COUNT", 20)
STUDY_MAX_CHUNKS = _int_env("STUDY_MAX_CHUNKS", 30)

# История чатов (Stage 19): сколько последних сообщений сессии берём в контекст
# LLM, чтобы не раздувать prompt. Сервер — источник правды по истории.
CHAT_HISTORY_CONTEXT_MESSAGES = _int_env("CHAT_HISTORY_CONTEXT_MESSAGES", 20)


# --- Email + account flows (Stage 23) -------------------------------------
# Public base URL the app is served from. Used to build links in emails
# (password reset / email verification). Override in production, e.g.
# https://vedomo.example.
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:3000").rstrip("/")

# CSRF defense (Stage 41): allowed Origins for state-changing requests. Cookie
# auth + SameSite=Lax already blocks classic cross-site POST; the Origin/Referer
# check (src/csrf.py) is defense-in-depth. Defaults to the public app URL plus
# localhost dev; override with a comma-separated TRUSTED_ORIGINS in production if
# the app is served from more than one origin.
TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "TRUSTED_ORIGINS",
        f"{APP_BASE_URL},http://localhost:3000,http://127.0.0.1:3000",
    ).split(",")
    if origin.strip()
]

# Email delivery backend (dual-backend, like the rest of the scaling plan):
#   "console" (default) - log the message + link to stdout; zero infra, for
#                         dev / CI / smoke (copy the link from the logs).
#   "smtp"              - real delivery via the SMTP_* settings (production).
#   "memory"            - collect into an in-process outbox (the test suite).
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "console").lower()
EMAIL_FROM = os.getenv("EMAIL_FROM", "Наставник <no-reply@localhost>")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = _int_env("SMTP_PORT", 587)
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes", "on")
# Implicit TLS on connect (SMTPS) - use for port 465, e.g. Yandex/Mail.ru. When
# false we connect plain and optionally STARTTLS (port 587). If both are set,
# SSL wins. Russian providers usually want SMTP_USE_SSL=true on port 465.
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "false").lower() in ("1", "true", "yes", "on")

# Single-use token lifetimes (minutes). Reset is short; verification is lax.
PASSWORD_RESET_TTL_MINUTES = _int_env("PASSWORD_RESET_TTL_MINUTES", 60)
EMAIL_VERIFY_TTL_MINUTES = _int_env("EMAIL_VERIFY_TTL_MINUTES", 60 * 24 * 3)

# Per-IP rate limit for the password-forgot/reset endpoints (slowapi).
RATE_LIMIT_PASSWORD = os.getenv("RATE_LIMIT_PASSWORD", "5/minute")


def is_production_like() -> bool:
    """Грубое определение «боевого» запуска: явный APP_ENV, Postgres-БД или
    включённый Secure-флаг куки. Используется, чтобы не дать стартовать с
    небезопасными значениями по умолчанию в проде, не мешая локальной разработке
    и CI (SQLite + dev-секрет)."""
    if os.getenv("APP_ENV", "").strip().lower() in ("production", "prod"):
        return True
    if DATABASE_URL.startswith("postgres"):
        return True
    if AUTH_COOKIE_SECURE:
        return True
    return False


def _jwt_secret_insecure_in_prod() -> bool:
    return is_production_like() and JWT_SECRET_KEY == INSECURE_JWT_DEFAULT


def assert_jwt_secret_safe() -> None:
    """Жёсткий отказ стартовать, если в проде используется dev-секрет JWT.

    Вызывается при импорте ``api_app``, поэтому срабатывает и при запуске через
    ``uvicorn api_app:app`` напрямую (мимо ``run_api.py``/``validate_config``).
    Дефолтный секрет лежит в публичном репозитории - с ним любой подделает JWT
    для любого пользователя."""
    if _jwt_secret_insecure_in_prod():
        raise RuntimeError(
            "FATAL: JWT_SECRET_KEY не задан - используется небезопасное значение "
            "по умолчанию в production-окружении. Задайте уникальный "
            "JWT_SECRET_KEY в переменных окружения и перезапустите."
        )


def validate_config():
    """Проверяет базовые настройки проекта."""
    errors = []

    if LLM_MODE not in ["api", "ollama", "gigachat"]:
        errors.append("LLM_MODE должен быть 'api', 'ollama' или 'gigachat'.")

    if _jwt_secret_insecure_in_prod():
        errors.append(
            "JWT_SECRET_KEY использует небезопасное значение по умолчанию в "
            "production-окружении. Задайте уникальный JWT_SECRET_KEY в .env - "
            "иначе любой сможет подделать токены сессий."
        )

    if LLM_MODE == "api" and not API_KEY:
        errors.append("Для режима api необходимо указать API_KEY в .env.")

    if LLM_MODE == "gigachat" and not GIGACHAT_AUTH_KEY:
        errors.append("Для режима gigachat необходимо указать GIGACHAT_AUTH_KEY в .env.")

    if CHUNK_OVERLAP >= CHUNK_SIZE:
        errors.append("CHUNK_OVERLAP должен быть меньше CHUNK_SIZE.")

    if RETRIEVAL_TOP_K < RERANK_TOP_K:
        errors.append("RETRIEVAL_TOP_K должен быть больше или равен RERANK_TOP_K.")

    return errors
