"""
document_loader.py - загрузка и извлечение текста из документов разных форматов.

Определение формата - по СОДЕРЖИМОМУ (магические байты), а не по расширению
(``detect_format``). Файл с неверным расширением (``lecture.pdf``, который на
деле .docx) всё равно прочитается верно, а скан/картинка/старый бинарный формат
дадут понятную ошибку. Распознавание сканов/картинок и старый .doc/.ppt живут в
:mod:`src.ocr` (нужны системные бинари; аккуратно деградируют, если их нет).
"""

import csv
import io
import os
import tempfile
import threading
import zipfile

import config
from src import ocr
from src.text_repair import repair_scanned_text


# Thread-local: помечаем, использовал ли последний load_file() OCR (скан или
# картиночные слайды). create_document читает это сразу после извлечения (тот же
# поток, синхронно) и выставляет Document.ocr_used, чтобы UI показал плашку
# «распознано с картинок - формулы/схемы сверяйте с оригиналом».
_ocr_state = threading.local()


def _reset_ocr_flag() -> None:
    _ocr_state.used = False
    _ocr_state.pages = []


def _mark_ocr_used() -> None:
    _ocr_state.used = True


def last_load_used_ocr() -> bool:
    return bool(getattr(_ocr_state, "used", False))


def last_load_pages() -> list[tuple[int, str]]:
    """Страницы последнего PDF в текущем потоке.

    Возвращаем копию списка: индексатор может добавлять служебную разметку, но
    состояние загрузчика не должно от этого меняться. Для не-PDF список пуст. В OCR-пути сохраняются физические номера
    страниц исходного PDF, включая пропуски пустых страниц.
    """
    return list(getattr(_ocr_state, "pages", []) or [])


class UnsupportedFormatError(ValueError):
    """Формат файла не распознан и расширение тоже неизвестно."""


# Картинки -> OCR. Распознаём по магическим байтам только надёжные (png/jpg/
# tiff); прочие (bmp) маршрутизируем по расширению.
IMAGE_FORMATS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp"}
# Старые бинарные Office-форматы -> конвертация через LibreOffice.
LEGACY_OFFICE = {".doc", ".ppt"}
# Презентации: если текст не извлёкся (слайды-картинки), рендерим в PDF и OCR-им.
PRESENTATION_FORMATS = {".ppt", ".pptx", ".odp"}
# ZIP-контейнеры: перед парсингом проверяем суммарный распакованный размер
# (защита от zip-бомбы, Stage 41).
ZIP_BASED_FORMATS = {
    ".docx", ".pptx", ".xlsx", ".odt", ".odp", ".ods", ".epub", ".fb2.zip",
}


# --- Текстовые / простые форматы ------------------------------------------

def load_txt(path):
    """Прочитать текстовый файл, пробуя разные кодировки."""
    for enc in ["utf-8", "cp1251", "latin-1", "cp866"]:
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError(f"Не удалось прочитать: {path}")


def load_csv(path):
    """Извлечь текст из CSV: каждую строку как «ячейка | ячейка» (Stage 40)."""
    text = load_txt(path)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    parts = []
    for row in csv.reader(io.StringIO(text), dialect):
        cells = [c.strip() for c in row if c and c.strip()]
        if cells:
            parts.append(" | ".join(cells))
    return "\n".join(parts)


def load_pdf(path):
    """Извлечь текст из PDF (текстовый слой). Скан-PDF без текстового слоя даёт
    почти пусто - решение про OCR принимает :func:`load_file`."""
    from pypdf import PdfReader

    reader = PdfReader(path)
    pages = []
    for page_number, page in enumerate(reader.pages, start=1):
        # Старый generator вызывал extract_text() дважды для каждой непустой
        # страницы. На 3156-страничном PDF это почти удваивало работу.
        text = page.extract_text() or ""
        if text.strip():
            pages.append((page_number, text))
    _ocr_state.pages = pages
    return "\n\n".join(text for _page, text in pages)


def load_docx(path):
    """Извлечь текст из Word-документа (.docx): абзацы и таблицы В ПОРЯДКЕ ДОКУМЕНТА.

    Раньше брались только ``paragraphs`` - текст в таблицах терялся (Stage 38).
    Потом таблицы добавились, но списком ПОСЛЕ всех абзацев, и это ломало
    ответы тише и хуже, чем прежняя потеря.

    Замер 04.09.2026. Одна и та же выдержка из свода правил с тремя таблицами,
    в PDF и в Word. Вопрос про коэффициент Kв при x/l=80: по PDF 8 верных
    ответов из 8, по Word 2 из 4. Ассистент отвечал так: «коэффициент можно
    найти в таблице Ж.3, однако конкретные значения из этой таблицы в
    представленном контексте отсутствуют». Подпись таблицы осталась на своём
    месте в тексте, сама таблица уехала в конец файла и попала в другой
    фрагмент - поиск приносил подпись без чисел.

    Вдобавок таблицы слипались: последняя строка одной шла вплотную к шапке
    следующей, и границу между ними было не найти.

    Три изменения:

    * обходим тело документа по порядку, поэтому таблица стоит там, где она и
      в документе, - рядом со своей подписью;
    * пустые ячейки сохраняем. Раньше стояло ``if c.text.strip()``, и строка
      «Иванов | (пусто) | 5» превращалась в «Иванов | 5»: значение уезжало в
      соседнюю колонку. В матрице ответственности или в тарифе так меняется
      смысл строки, а выглядит это как обычные данные;
    * таблица идёт одним блоком, поэтому между соседними таблицами остаётся
      пустая строка.
    """
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(path)
    parts = []
    for child in doc.element.body.iterchildren():
        tag = child.tag
        if tag.endswith("}p"):
            text = Paragraph(child, doc).text.strip()
            if text:
                parts.append(text)
        elif tag.endswith("}tbl"):
            rows = []
            for row in Table(child, doc).rows:
                cells = [c.text.strip() for c in row.cells]
                # Строку без единого заполненного поля пропускаем: это
                # разделитель вёрстки, а не данные.
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                parts.append("\n".join(rows))
    return "\n\n".join(parts)


def load_pptx(path):
    """Извлечь текст из презентации PowerPoint (.pptx): слайды + таблицы (Stage 38)."""
    from pptx import Presentation

    prs = Presentation(path)
    parts = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                text = shape.text_frame.text.strip()
                if text:
                    parts.append(text)
            if shape.has_table:
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if cells:
                        parts.append(" | ".join(cells))
    return "\n\n".join(parts)


def load_rtf(path):
    """Извлечь текст из RTF (Stage 38). Читаем как latin-1, чтобы striprtf сам
    раскодировал \\'xx-последовательности по кодовой странице документа."""
    from striprtf.striprtf import rtf_to_text

    with open(path, "r", encoding="latin-1") as f:
        return rtf_to_text(f.read())


# --- OpenDocument (LibreOffice / OpenOffice), Stage 40 --------------------

def _load_odf(path, spreadsheet=False):
    """Общий извлекатель для .odt/.odp/.ods через odfpy."""
    from odf.opendocument import load
    from odf import teletype
    from odf.text import P, H
    from odf.table import Table, TableRow, TableCell

    doc = load(path)
    parts = []
    if not spreadsheet:
        # Текстовые документы / презентации: абзацы и заголовки.
        for el in doc.getElementsByType(H) + doc.getElementsByType(P):
            t = teletype.extractText(el).strip()
            if t:
                parts.append(t)
    # Таблицы (всегда для .ods; для .odt/.odp - данные в таблицах тоже важны).
    for table in doc.getElementsByType(Table):
        for trow in table.getElementsByType(TableRow):
            cells = [
                teletype.extractText(c).strip()
                for c in trow.getElementsByType(TableCell)
            ]
            cells = [c for c in cells if c]
            if cells:
                parts.append(" | ".join(cells))
    return "\n\n".join(parts)


def load_odt(path):
    return _load_odf(path, spreadsheet=False)


def load_odp(path):
    return _load_odf(path, spreadsheet=False)


def load_ods(path):
    return _load_odf(path, spreadsheet=True)


# --- Таблицы Excel, Stage 40 ----------------------------------------------

def load_xlsx(path):
    """Извлечь текст из .xlsx: каждый лист - строки «ячейка | ячейка»."""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    parts = []
    for ws in wb.worksheets:
        parts.append(f"# {ws.title}")
        for row in ws.iter_rows(values_only=True):
            cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
            if cells:
                parts.append(" | ".join(cells))
    wb.close()
    return "\n".join(parts)


def load_xls(path):
    """Извлечь текст из старого .xls (OLE) через xlrd."""
    import xlrd

    book = xlrd.open_workbook(path)
    parts = []
    for sheet in book.sheets():
        parts.append(f"# {sheet.name}")
        for r in range(sheet.nrows):
            cells = [
                str(sheet.cell_value(r, c)).strip()
                for c in range(sheet.ncols)
            ]
            cells = [c for c in cells if c]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


# --- Книги / разметка ------------------------------------------------------

def load_epub(path):
    """Извлечь текст из EPUB."""
    import ebooklib
    from ebooklib import epub
    from bs4 import BeautifulSoup

    book = epub.read_epub(path)
    parts = []

    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), "html.parser")
        text = soup.get_text(separator="\n")
        if text.strip():
            parts.append(text.strip())

    return "\n\n".join(parts)


def load_fb2(path):
    """Извлечь текст из FB2."""
    from bs4 import BeautifulSoup

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        soup = BeautifulSoup(f.read(), "lxml-xml")

    body = soup.find("body")
    return body.get_text(separator="\n") if body else soup.get_text(separator="\n")


def load_fb2zip(path):
    """Распаковать ZIP и извлечь текст из FB2 внутри архива."""
    with zipfile.ZipFile(path, "r") as zf:
        fb2_files = [
            name for name in zf.namelist()
            if name.lower().endswith(".fb2")
        ]

        if not fb2_files:
            raise ValueError(f"Нет .fb2 внутри архива: {path}")

        with tempfile.TemporaryDirectory() as tmpdir:
            zf.extract(fb2_files[0], tmpdir)
            return load_fb2(os.path.join(tmpdir, fb2_files[0]))


def load_html(path):
    """Извлечь текст из HTML."""
    from bs4 import BeautifulSoup

    text = load_txt(path)
    return BeautifulSoup(text, "html.parser").get_text(separator="\n")


LOADERS = {
    ".pdf": load_pdf,
    ".txt": load_txt,
    ".md": load_txt,
    ".csv": load_csv,
    ".docx": load_docx,
    ".pptx": load_pptx,
    ".rtf": load_rtf,
    ".odt": load_odt,
    ".odp": load_odp,
    ".ods": load_ods,
    ".xlsx": load_xlsx,
    ".xls": load_xls,
    ".epub": load_epub,
    ".fb2": load_fb2,
    ".fb2.zip": load_fb2zip,
    ".html": load_html,
    ".htm": load_html,
}


# --- Определение формата по содержимому (Stage 40) ------------------------

def _sniff_zip(path):
    """ZIP-контейнер: понять, что внутри (OOXML / OpenDocument / epub / fb2.zip)."""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if "mimetype" in names:
                try:
                    mt = z.read("mimetype").decode("ascii", "ignore").strip()
                except Exception:
                    mt = ""
                mime_map = {
                    "application/epub+zip": ".epub",
                    "application/vnd.oasis.opendocument.text": ".odt",
                    "application/vnd.oasis.opendocument.presentation": ".odp",
                    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
                }
                if mt in mime_map:
                    return mime_map[mt]
            if any(n.startswith("word/") for n in names):
                return ".docx"
            if any(n.startswith("ppt/") for n in names):
                return ".pptx"
            if any(n.startswith("xl/") for n in names):
                return ".xlsx"
            if any(n.lower().endswith(".fb2") for n in names):
                return ".fb2.zip"
    except (zipfile.BadZipFile, OSError):
        return None
    return None


def _sniff_ole(path):
    """Старый бинарный Office (OLE/CFB). Различаем doc/xls/ppt по именам потоков
    (хранятся как UTF-16LE) - это эвристика, но в большинстве файлов работает."""
    try:
        with open(path, "rb") as f:
            blob = f.read(16384)
    except OSError:
        return None
    if b"W\x00o\x00r\x00d\x00D\x00o\x00c\x00u\x00m\x00e\x00n\x00t" in blob:
        return ".doc"
    if b"W\x00o\x00r\x00k\x00b\x00o\x00o\x00k" in blob:
        return ".xls"
    if b"P\x00o\x00w\x00e\x00r\x00P\x00o\x00i\x00n\x00t" in blob:
        return ".ppt"
    # OLE, но поток не опознан в первых 16 КБ: возвращаем None, чтобы решало
    # расширение (Stage 41). Иначе настоящий .xls с «дальним» Workbook-потоком
    # ушёл бы в LibreOffice вместо xlrd.
    return None


def sniff_format(path):
    """Определить формат по магическим байтам. Возвращает канонический ключ
    ('.pdf', '.docx', ...) или None, если по содержимому не опознан (тогда
    решает расширение - это нормально для .txt/.md/.csv)."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return None
    if not head:
        return None

    if head[:5] == b"%PDF-" or b"%PDF-" in head[:1024]:
        return ".pdf"
    if head[:5] == b"{\\rtf":
        return ".rtf"
    if head[:2] == b"PK":
        return _sniff_zip(path)
    if head[:8] == b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1":
        return _sniff_ole(path)
    if head[:4] == b"AT&T":
        return ".djvu"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if head[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return ".tiff"

    # XML-разметка: FB2 или HTML.
    low = head.lstrip()[:512].lower()
    if low.startswith(b"<?xml"):
        if b"<fictionbook" in head.lower():
            return ".fb2"
        if b"<html" in low or b"<!doctype html" in low:
            return ".html"
        return None  # прочий XML - пусть решает расширение
    if low.startswith(b"<!doctype html") or low.startswith(b"<html"):
        return ".html"
    return None


def detect_format(path):
    """Канонический формат файла: сначала по содержимому, затем по расширению.

    Возвращает ключ ('.pdf', '.docx', '.png', ...) или None, если формат
    неизвестен и так, и так."""
    sniffed = sniff_format(path)
    if sniffed:
        return sniffed

    lower = path.lower()
    if lower.endswith(".fb2.zip"):
        return ".fb2.zip"
    ext = os.path.splitext(path)[1].lower()
    if ext in LOADERS or ext in IMAGE_FORMATS or ext in LEGACY_OFFICE or ext == ".djvu":
        return ext
    return None


def _looks_like_scan(text):
    """PDF без извлекаемого текста (или почти) - это скан из картинок."""
    return len(text.strip()) < 20


# Ниже этого объёма текста многослайдовая презентация почти наверняка
# картиночная (у текстовых - тысячи символов), стоит попробовать render+OCR.
# Защита «берём, только если богаче» ниже не даёт ложному срабатыванию навредить.
_PRESENTATION_MIN_TEXT = 200


def _ocr_presentation_fallback(path, ext, text):
    """Презентация дала мало текста -> слайды, вероятно, картиночные. Рендерим её
    в PDF через LibreOffice и распознаём (OCR). Берём распознанное, только если
    оно богаче исходного (чтобы не испортить презу, где текст и так был); при
    недоступном LibreOffice/OCR тихо оставляем исходный текст (без regress).
    Срабатывание и исход логируем в ``vedomo.loader`` для диагностики.
    """
    import logging

    log = logging.getLogger("vedomo.loader")
    if len(text.strip()) >= _PRESENTATION_MIN_TEXT:
        return text
    log.info(
        "presentation %s: only %d chars extracted - rendering via LibreOffice + OCR",
        ext, len(text.strip()),
    )
    try:
        ocr_text = ocr.ocr_office_via_pdf(path, ext)
    except ocr.ExtractionUnavailableError as e:
        log.warning("presentation %s OCR fallback failed: %s", ext, e)
        return text
    log.info(
        "presentation %s OCR fallback produced %d chars (was %d)",
        ext, len(ocr_text.strip()), len(text.strip()),
    )
    if len(ocr_text.strip()) > len(text.strip()):
        _mark_ocr_used()
        return ocr_text
    return text


def _guard_zip_size(path):
    """Защита от «zip-бомбы» (Stage 41): отказываемся парсить, если суммарный
    распакованный размер превышает ``config.MAX_ZIP_UNCOMPRESSED_BYTES``. Битый/
    не-zip пропускаем - пусть конкретный загрузчик разбирается сам."""
    try:
        with zipfile.ZipFile(path) as z:
            total = sum(info.file_size for info in z.infolist())
    except (zipfile.BadZipFile, OSError):
        return
    if total > config.MAX_ZIP_UNCOMPRESSED_BYTES:
        mb = total // (1024 * 1024)
        raise ValueError(
            f"файл слишком большой в распакованном виде (~{mb} МБ), похоже на "
            "архив-бомбу - загрузите файл меньшего объёма"
        )


# --- Главная точка входа ---------------------------------------------------

def load_file(path):
    """Определить формат файла (по содержимому) и извлечь текст.

    Картинки и скан-PDF уходят в OCR; старый .doc/.ppt - в конвертацию; всё это
    аккуратно деградирует, если на сервере нет нужного бинаря (см. :mod:`src.ocr`).
    Неизвестный формат -> :class:`UnsupportedFormatError`."""
    _reset_ocr_flag()
    fmt = detect_format(path)
    if fmt is None:
        ext = os.path.splitext(path)[1].lower() or "(без расширения)"
        raise UnsupportedFormatError(f"Неподдерживаемый формат: {ext}")

    if fmt in IMAGE_FORMATS:
        _mark_ocr_used()
        return repair_scanned_text(ocr.ocr_image(path))
    if fmt == ".djvu":
        _mark_ocr_used()
        return repair_scanned_text(ocr.ocr_djvu(path))
    if fmt in LEGACY_OFFICE:
        if fmt in PRESENTATION_FORMATS:  # .ppt: текст ИЛИ render+OCR картиночных слайдов
            try:
                text = ocr.convert_legacy_office(path, fmt)
            except ocr.ExtractionUnavailableError:
                # LibreOffice не дал текст (битый/картиночный слайд-формат) - не
                # сдаёмся: рендерим в PDF и распознаём ниже, а не падаем ошибкой.
                text = ""
            return _ocr_presentation_fallback(path, fmt, text)
        return ocr.convert_legacy_office(path, fmt)  # .doc

    if fmt == ".pdf":
        text = load_pdf(path)
        if _looks_like_scan(text):
            # Текстового слоя нет - распознаём как скан (или понятная ошибка,
            # если OCR на сервере не настроен).
            _mark_ocr_used()
            pages = [
                (number, repair_scanned_text(page_text))
                for number, page_text in ocr.ocr_pdf_pages(path)
            ]
            _ocr_state.pages = pages
            return "\n\n".join(page_text for _number, page_text in pages)
        # Текстовый слой есть, но у сканов его писал чужой OCR: чиним переносы и
        # подменённые буквы, иначе «nри» и «при» уедут в индекс разными токенами.
        repaired_pages = [
            (page_number, repair_scanned_text(page_text))
            for page_number, page_text in last_load_pages()
        ]
        _ocr_state.pages = repaired_pages
        return "\n\n".join(page_text for _page_number, page_text in repaired_pages)

    if fmt in ZIP_BASED_FORMATS:
        _guard_zip_size(path)
    text = LOADERS[fmt](path)
    if fmt in PRESENTATION_FORMATS:  # .pptx/.odp: картиночные слайды -> render+OCR
        text = _ocr_presentation_fallback(path, fmt, text)
    return text
