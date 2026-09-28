"""
ocr.py - распознавание текста из сканов/картинок и конвертация старых бинарных
форматов (.doc/.ppt) через системные утилиты.

Тяжёлые зависимости здесь - не pip-пакеты, а системные бинари: ``tesseract``
(OCR) и ``libreoffice``/``soffice`` (конвертация старого Office), плюс
``ddjvu`` (djvulibre) для .djvu. Они ставятся на сервере (apt), а не через
requirements.txt.

Принцип: код всегда на месте, но аккуратно деградирует. Если бинаря нет -
поднимаем ``ExtractionUnavailableError`` с понятным пользователю текстом, а не
падаем стектрейсом. На VPS бинари есть - распознавание включается само, без
изменений кода. Рендер PDF-страниц делаем через ``pypdfium2`` (лицензия
BSD/Apache, ставится pip-ом) - намеренно НЕ PyMuPDF (AGPL, не годится для
коммерческого продукта).
"""
import functools
import os
import shutil
import subprocess
import tempfile
import re
import io
import logging
from src.model_scheduler import scheduler as model_scheduler

logger = logging.getLogger("vedomo.ocr")


class ExtractionUnavailableError(ValueError):
    """Формат распознан, но извлечь текст нельзя: нужен системный бинарь
    (tesseract/libreoffice/ddjvu), которого нет, либо OCR дал пусто.

    Сообщение пишется сразу понятным пользователю - вызывающий код может
    вернуть ``str(e)`` как есть."""


# Языки распознавания: русский + английский (учебные материалы кафедры).
OCR_LANG = os.getenv("OCR_LANG", "rus+eng")
# Потолок страниц для OCR одного PDF/DjVu - распознавание медленное (секунды на
# страницу даже на сервере). Сверх потолка распознаём первые N и помечаем.
OCR_PDF_MAX_PAGES = int(os.getenv("OCR_PDF_MAX_PAGES", "50"))
# Масштаб рендера страницы PDF -> картинка перед OCR (≈150 dpi при 2.0).
OCR_PDF_SCALE = float(os.getenv("OCR_PDF_SCALE", "2.0"))
OCR_PDF_RECHECK_SCALE = float(os.getenv("OCR_PDF_RECHECK_SCALE", "4.0"))
OCR_VISION_RECHECK_ENABLED = os.getenv(
    "OCR_VISION_RECHECK_ENABLED", "false"
).lower() in ("1", "true", "yes", "on")

_SUSPICIOUS_OCR_NUMBER = re.compile(
    r"(?<!\d)(?:14|12|34)\s*[-–]\s*\d|"
    r"\d\s*[-–]\s*(?:14|12|34)(?!\d)|"
    r"\b1\s*%\s*(?:quarts?|кварт[а-я]*)|"
    r"\b3\s*[-–]\s*4\s+(?:quarts?|кварт[а-я]*)"
)


@functools.lru_cache(maxsize=1)
def tesseract_binary() -> str | None:
    """Resolve Tesseract from config, PATH, or standard Windows locations."""
    configured = os.getenv("TESSERACT_CMD", "").strip()
    if configured and os.path.isfile(configured):
        return configured
    found = shutil.which("tesseract")
    if found:
        return found
    if os.name == "nt":
        roots = [
            os.getenv("ProgramFiles", r"C:\Program Files"),
            os.path.join(os.getenv("LOCALAPPDATA", ""), "Programs"),
        ]
        for root in roots:
            candidate = os.path.join(root, "Tesseract-OCR", "tesseract.exe")
            if os.path.isfile(candidate):
                return candidate
    return None


@functools.lru_cache(maxsize=1)
def tessdata_directory() -> str | None:
    """Optional language-data override; system defaults remain valid on Linux."""
    configured = os.getenv("TESSDATA_PREFIX", "").strip()
    if configured and os.path.isdir(configured):
        return configured
    if os.name == "nt":
        local = os.path.join(os.getenv("LOCALAPPDATA", ""), "Vedomo", "tessdata")
        if os.path.isdir(local):
            return local
    return None


def _prepare_pytesseract(pytesseract) -> None:
    binary = tesseract_binary()
    if binary:
        pytesseract.pytesseract.tesseract_cmd = binary
    data_dir = tessdata_directory()
    if data_dir:
        # Tesseract on Windows treats quotes in --tessdata-dir as literal in
        # some builds.  The native environment variable avoids that parser.
        os.environ["TESSDATA_PREFIX"] = data_dir


def _image_to_string(pytesseract, image, *, lang: str, extra_config: str = "") -> str:
    _prepare_pytesseract(pytesseract)
    options = []
    if extra_config:
        options.append(extra_config)
    kwargs = {"lang": lang}
    if options:
        kwargs["config"] = " ".join(options)
    return pytesseract.image_to_string(image, **kwargs)


def _numeric_ocr_suspicion_score(text: str) -> int:
    """Count common lost-fraction shapes (¼/½/¾ rendered as 14/12/34)."""
    return len(_SUSPICIOUS_OCR_NUMBER.findall(text or ""))


def _vision_ocr_page(page) -> str:
    """Exact page transcription via the configured vision provider."""
    from src.llm_engine import transcribe_image

    bitmap = page.render(scale=max(3.0, OCR_PDF_SCALE))
    pil = bitmap.to_pil()
    try:
        payload = io.BytesIO()
        pil.save(payload, format="PNG")
        return transcribe_image(payload.getvalue(), "image/png").strip()
    finally:
        pil.close()


def _recheck_numeric_ocr(page, first_text: str, *, lang: str) -> str:
    """Repeat only suspicious pages at higher DPI and keep the safer reading."""
    first_score = _numeric_ocr_suspicion_score(first_text)
    if not first_score:
        return first_text
    import pytesseract

    bitmap = page.render(scale=OCR_PDF_RECHECK_SCALE)
    pil = bitmap.to_pil()
    try:
        second = _image_to_string(
            pytesseract, pil, lang=lang, extra_config="--psm 6"
        ).strip()
    finally:
        pil.close()
    second_score = _numeric_ocr_suspicion_score(second)
    best = second if second and second_score < first_score else first_text
    best_score = min(first_score, second_score) if second else first_score
    if best_score == 0:
        return best

    if OCR_VISION_RECHECK_ENABLED:
        try:
            vision = _vision_ocr_page(page)
            vision_score = _numeric_ocr_suspicion_score(vision)
            # A very short vision response is likely a caption, not a page
            # transcription.  Never replace usable OCR with it.
            if len(vision) >= 100 and vision_score < best_score:
                return vision
        except Exception:
            # The OCR result remains usable and explicitly marked uncertain;
            # provider downtime must not fail the whole upload.
            logger.exception("vision OCR recheck failed; keeping uncertain Tesseract text")
    return (
        best.rstrip()
        + "\n[OCR: числовой фрагмент на этой странице распознан неуверенно; "
        "точное значение нужно сверить с оригиналом.]"
    )


def _check_ocr_page_limit(total: int, max_pages: int) -> None:
    """Не выдаём частичное OCR за полностью готовый документ."""
    if total <= max_pages:
        return
    raise ExtractionUnavailableError(
        f"В скане {total} страниц, а текущий безопасный лимит OCR — "
        f"{max_pages}. Файл не проиндексирован частично. Разделите скан на "
        "части или увеличьте OCR_PDF_MAX_PAGES на сервере после замера "
        "производительности."
    )


@functools.lru_cache(maxsize=1)
def tesseract_available() -> bool:
    """Есть ли в системе бинарь tesseract (через PATH или TESSERACT_CMD)."""
    if not tesseract_binary():
        return False
    try:
        import pytesseract

        _prepare_pytesseract(pytesseract)
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


@functools.lru_cache(maxsize=1)
def libreoffice_binary():
    """Путь к soffice/libreoffice или None."""
    for name in ("soffice", "libreoffice"):
        path = shutil.which(name)
        if path:
            return path
    return None


@functools.lru_cache(maxsize=1)
def djvu_binary():
    """Путь к ddjvu (djvulibre) или None."""
    return shutil.which("ddjvu")


def ocr_image(path: str, lang: str = OCR_LANG) -> str:
    """Распознать текст с картинки (png/jpg/tiff/...). Требует tesseract."""
    if not tesseract_available():
        raise ExtractionUnavailableError(
            "Это изображение, а распознавание текста (OCR) на сервере не "
            "настроено. Загрузите текстовый файл (PDF/DOCX) или попросите "
            "администратора включить OCR (tesseract)."
        )
    import pytesseract
    from PIL import Image

    with Image.open(path) as img:
        text = _image_to_string(pytesseract, img, lang=lang).strip()
    if not text:
        raise ExtractionUnavailableError(
            "С изображения не удалось распознать текст (пусто). Возможно, на "
            "картинке нет текста или он слишком мелкий."
        )
    return text


def ocr_pdf_pages(path: str, lang: str = OCR_LANG, max_pages: int = OCR_PDF_MAX_PAGES) -> list[tuple[int, str]]:
    """Распознать текст со скана-PDF: рендерим страницы через pypdfium2 и
    прогоняем через tesseract. Требует tesseract."""
    if not tesseract_available():
        raise ExtractionUnavailableError(
            "Похоже, это скан (PDF из изображений страниц), а распознавание "
            "текста (OCR) на сервере не настроено. Загрузите текстовый PDF/DOCX "
            "или попросите администратора включить OCR (tesseract)."
        )
    import pypdfium2 as pdfium
    import pytesseract

    pdf = pdfium.PdfDocument(path)
    try:
        total = len(pdf)
        _check_ocr_page_limit(total, max_pages)
        limit = total
        parts = []
        for i in range(limit):
            page = pdf[i]
            with model_scheduler.indexing():
                bitmap = page.render(scale=OCR_PDF_SCALE)
                pil = bitmap.to_pil()
                try:
                    chunk = _image_to_string(pytesseract, pil, lang=lang).strip()
                finally:
                    pil.close()
                if chunk:
                    chunk = _recheck_numeric_ocr(page, chunk, lang=lang)
            if chunk:
                parts.append((i + 1, chunk))
    finally:
        pdf.close()

    if not parts:
        raise ExtractionUnavailableError(
            "Это скан, но распознавание не дало текста. Возможно, низкое "
            "качество скана - попробуйте текстовый файл."
        )
    return parts


def ocr_pdf(path: str, lang: str = OCR_LANG, max_pages: int = OCR_PDF_MAX_PAGES) -> str:
    """Совместимый текстовый интерфейс для OCR, включая Office-конвертацию."""
    return "\n\n".join(text for _page, text in ocr_pdf_pages(path, lang, max_pages))


def ocr_djvu(path: str, lang: str = OCR_LANG, max_pages: int = OCR_PDF_MAX_PAGES) -> str:
    """Распознать .djvu: рендерим страницы в TIFF через ddjvu, затем OCR.
    Требует ddjvu (djvulibre) + tesseract."""
    exe = djvu_binary()
    if not exe or not tesseract_available():
        raise ExtractionUnavailableError(
            "Это .djvu (обычно скан-книга). Для него нужны системные утилиты "
            "djvulibre + tesseract, которые на сервере не настроены. Загрузите "
            "текстовый PDF/DOCX или попросите администратора их включить."
        )
    import pytesseract
    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        tif = os.path.join(tmp, "page.tif")
        try:
            subprocess.run(
                [exe, "-format=tiff", "-quality=85", path, tif],
                check=True,
                timeout=300,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.SubprocessError, OSError) as e:
            raise ExtractionUnavailableError(
                f"Не удалось обработать .djvu: {e}"
            ) from e

        parts = []
        with Image.open(tif) as img:
            _check_ocr_page_limit(int(getattr(img, "n_frames", 1) or 1), max_pages)
            page = 0
            while page < max_pages:
                try:
                    img.seek(page)
                except EOFError:
                    break
                chunk = _image_to_string(pytesseract, img, lang=lang).strip()
                if chunk:
                    parts.append(chunk)
                page += 1
        text = "\n\n".join(parts)

    if not text.strip():
        raise ExtractionUnavailableError(
            "Из .djvu не удалось распознать текст (пусто)."
        )
    return text


def convert_legacy_office(path: str, ext: str) -> str:
    """Сконвертировать старый бинарный .doc/.ppt в текст через LibreOffice
    (headless). Требует soffice/libreoffice."""
    exe = libreoffice_binary()
    if not exe:
        raise ExtractionUnavailableError(
            f"Это старый формат {ext} (бинарный Office). Автоконвертация на "
            "сервере не настроена - пересохраните файл как .docx/.pptx/.pdf "
            "или попросите администратора включить LibreOffice."
        )
    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run(
                [exe, "--headless", "--convert-to", "txt:Text", "--outdir", tmp, path],
                check=True,
                timeout=180,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.SubprocessError, OSError) as e:
            raise ExtractionUnavailableError(
                f"LibreOffice не смог конвертировать {ext}: {e}"
            ) from e

        out = os.path.join(
            tmp, os.path.splitext(os.path.basename(path))[0] + ".txt"
        )
        if not os.path.exists(out):
            raise ExtractionUnavailableError(
                f"LibreOffice не создал текст из {ext}."
            )
        for enc in ("utf-8", "cp1251", "latin-1"):
            try:
                with open(out, "r", encoding=enc) as f:
                    return f.read()
            except (UnicodeDecodeError, UnicodeError):
                continue
    raise ExtractionUnavailableError(f"Не удалось прочитать конвертацию {ext}.")


def ocr_office_via_pdf(path: str, ext: str) -> str:
    """Картиночная презентация/документ: рендерим в PDF через LibreOffice и
    распознаём страницы (OCR).

    Используется как запасной путь, когда обычное извлечение текста дало почти
    пусто - значит текст на слайдах «нарисован» (в составе картинок/объектов), а
    не лежит в текстовых полях. LibreOffice рендерит слайды как есть (включая
    встроенные/связанные картинки и WordArt), pypdfium2 растрит страницы,
    tesseract читает текст. Требует soffice + tesseract."""
    exe = libreoffice_binary()
    if not exe or not tesseract_available():
        raise ExtractionUnavailableError(
            f"Похоже, {ext} состоит из картинок (текст не извлёкся как текст), а "
            "для распознавания нужны LibreOffice + OCR, которых нет на сервере."
        )
    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run(
                [exe, "--headless", "--convert-to", "pdf", "--outdir", tmp, path],
                check=True,
                timeout=300,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.SubprocessError, OSError) as e:
            raise ExtractionUnavailableError(
                f"LibreOffice не смог отрендерить {ext} в PDF: {e}"
            ) from e
        pdf_path = os.path.join(
            tmp, os.path.splitext(os.path.basename(path))[0] + ".pdf"
        )
        if not os.path.exists(pdf_path):
            raise ExtractionUnavailableError(f"LibreOffice не создал PDF из {ext}.")
        # Переиспользуем готовый OCR PDF-страниц (рендер + tesseract, потолок страниц).
        return ocr_pdf(pdf_path)
