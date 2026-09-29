"""
text_processing.py - обработка текста: разделы, очистка, чанкинг.
"""

import re
import config


def _normalize_line(line):
    """Нормализует строку PDF-текста."""
    line = line.replace("\xa0", " ")
    line = line.replace("\u2009", " ")
    line = line.replace("\u202f", " ")
    line = re.sub(r"\s+", " ", line).strip()

    # PDF иногда даёт "Глава 1 3" вместо "Глава 13"
    line = re.sub(
        r"\b(ГЛАВА|Глава|глава)\s+(\d)\s+(\d)\b",
        r"\1 \2\3",
        line,
    )

    # PDF иногда даёт "Глава 1 0" вместо "Глава 10"
    line = re.sub(
        r"\b(ГЛАВА|Глава|глава)\s+(\d)\s+(\d)\s*\.",
        r"\1 \2\3.",
        line,
    )

    return line


def _is_noise_header(line):
    """Отсекает строки, которые похожи на заголовки, но ими не являются."""
    low = line.lower()

    noise_words = [
        "оглавление",
        "содержание",
        "список иллюстраций",
        "список литературы",
        "isbn",
        "министерство науки",
        "издательство",
        "автор фото",
        "риа новости",
        "цит. по",
        "цит по",
    ]

    if any(word in low for word in noise_words):
        return True

    if line.startswith("("):
        return True

    if line.endswith("-"):
        return True

    if re.match(r"^\d{1,3}\.\s+", line):
        return True

    # Строки оглавления обычно содержат много точек
    if line.count(".....") >= 1 or line.count(". . .") >= 1:
        return True

    return False


# Строка оглавления: точечный «лидер» (4+ точек, можно через пробелы),
# заканчивающийся номером страницы, напр. "§ 6. Дифференциальные уравнения . . . . . 54".
# Такие строки совпадают по словам с кучей запросов, но смысла не несут и засоряют
# поиск (eval Stage 36 поймал именно это - бот получал оглавление вместо текста).
_TOC_LINE_RE = re.compile(r"\.(\s*\.){3,}\s*\d{1,4}\s*$")


def _is_toc_line(line):
    """Похожа ли строка на пункт оглавления (лидер из точек + номер страницы)."""
    return bool(_TOC_LINE_RE.search(line.strip()))


# Издательский колофон / выходные данные (тираж, "подписано к печати", редактор,
# адрес типографии). Такой же мусор для поиска, как оглавление: совпадает по
# словам, но содержания не несёт (Stage 37: eval поймал, что бот получал
# страницу выходных данных вместо текста). Маркеры узкие - в самом тексте
# учебника такие обороты практически не встречаются.
_IMPRINT_RE = re.compile(
    r"подписано\s+к\s+печати"
    r"|тираж\s+\d+\s*экз"
    r"|\bпеч\.?\s*л\."
    r"|усл\.?\s*печ"
    r"|план\s+издания"
    r"|отпечатано\s+в\b"
    r"|редакционно-издательск"
    r"|компьютерная\s+в[её]рстка",
    re.IGNORECASE,
)


def _is_imprint_line(line):
    """Похожа ли строка на выходные данные/колофон (издательский мусор)."""
    return bool(_IMPRINT_RE.search(line))


def _is_header(line):
    """
    Определяет, является ли строка заголовком раздела.

    Поддерживает:
    - ГЛАВА 13. ...
    - Глава 13. ...
    - Глава 1 3. ...
    - § 64. ...
    - § 81 Отражение ...
    - РОССИЯ 1990-Х ГОДОВ
    """
    line = _normalize_line(line)

    if not line:
        return False

    if _is_noise_header(line):
        return False

    if len(line) > 180:
        return False

    if re.match(r"^(ГЛАВА|Глава|глава)\s+\d{1,2}\s*[\.\-–:]?$", line):
        return True

    # ГЛАВА 13 или ГЛАВА 13. ВЕЛИКАЯ РОССИЙСКАЯ РЕВОЛЮЦИЯ...
    if re.match(r"^(ГЛАВА|Глава|глава)\s+\d{1,2}\s*[\.\-–:]?(?:\s+.{5,})?$", line):
        return True

    # § 64 или § 64. Причины революционного кризиса 1917 г.
    if re.match(r"^§\s*\d{1,3}\s*[\.\-–:]?(?:\s+.{5,})?$", line):
        return True

    # Раздел / Часть
    if re.match(r"^(РАЗДЕЛ|Раздел|ЧАСТЬ|Часть)\s+[\dIVXLCDMivxlcdm]+.*$", line):
        return True

    # Заголовки капсом: РОССИЯ 1990-Х ГОДОВ.
    # Считаем долю заглавных среди всех букв, иначе строки с аббревиатурами
    # вроде "СССР и РККА..." ошибочно выглядят как заголовки.
    letters = re.sub(r"[^А-Яа-яЁёA-Za-z]", "", line)
    if len(letters) >= 8:
        upper_ratio = sum(1 for ch in letters if ch.isupper()) / max(len(letters), 1)
        if upper_ratio > 0.85 and len(line) <= 120:
            return True

    return False


def is_user_visible_section(line):
    """
    Строгая проверка заголовка для UI и навигации.

    Более консервативна, чем общий детектор разделов:
    пропускает главы/параграфы/подразделы и осмысленные тематические заголовки,
    но отсекает формулы, библиографию, списки авторов и технический мусор PDF.
    """
    line = _normalize_line(line)

    if not line:
        return False

    if _is_noise_header(line):
        return False

    if len(line) > 140:
        return False

    low = line.lower()

    if re.search(r"[=<>]|https?://|www\.|/[A-Z]|\\", line):
        return False

    if re.search(r"\b[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё\-]+,\s*[A-ZА-ЯЁ]\.", line):
        return False

    if line.count(",") >= 2 or line.count(";") >= 2:
        return False

    if re.match(r"^(ГЛАВА|Глава|глава)\s+\d{1,2}\s*[\.\-–:]?(?:\s+.{0,100})?$", line):
        return True

    if re.match(r"^§\s*\d{1,3}\s*[\.\-–:]?(?:\s+.{0,100})?$", line):
        return True

    if re.match(r"^(РАЗДЕЛ|Раздел|ЧАСТЬ|Часть)\s+[\dIVXLCDMivxlcdm]+.*$", line):
        return True

    if re.match(r"^\d+(?:\.\d+){1,3}\.?\s+.{4,100}$", line):
        return True

    if sum(ch.isdigit() for ch in line) >= 8 and "глава" not in low and "§" not in line:
        return False

    if re.match(r"^(ВВЕДЕНИЕ|ЗАКЛЮЧЕНИЕ|ПРЕДИСЛОВИЕ|ПРИЛОЖЕНИЕ)\b", line.upper()):
        return True

    letters = re.sub(r"[^А-Яа-яЁёA-Za-z]", "", line)
    if len(letters) >= 8:
        upper_ratio = sum(1 for ch in letters if ch.isupper()) / max(len(letters), 1)
        word_count = len(re.findall(r"[А-ЯЁA-Z][А-ЯЁA-Z0-9\-]{1,}", line))
        if upper_ratio > 0.88 and 1 <= word_count <= 8 and line.count(",") == 0:
            return True

    return False


def detect_sections(text):
    """
    Разбивает текст на разделы по заголовкам.

    Важно:
    старый вариант требовал пустую строку до и после заголовка.
    Для PDF это плохо работает, поэтому здесь заголовки ищутся без требования пустых строк.
    """
    lines = text.split("\n")

    sections = []
    current_header = ""
    current_lines = []

    for line in lines:
        normalized = _normalize_line(line)

        if _is_header(normalized) and is_user_visible_section(normalized):
            if current_lines:
                body = "\n".join(current_lines).strip()
                if body:
                    sections.append((current_header, body))

            current_header = normalized
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        body = "\n".join(current_lines).strip()
        if body:
            sections.append((current_header, body))

    # Если разделов почти нет, возвращаем весь текст как один раздел
    real_sections = [name for name, _ in sections if name.strip()]

    if len(real_sections) <= 1:
        return [("", text)]

    return sections


def clean_sections(sections):
    """Очищает текст разделов от лишних пробелов и переносов."""
    cleaned = []

    for name, body in sections:
        name = _normalize_line(name)

        body = body.replace("\xa0", " ")
        body = body.replace("\u2009", " ")
        body = body.replace("\u202f", " ")
        body = re.sub(r"\n{3,}", "\n\n", body)
        body = re.sub(r" {2,}", " ", body)
        # Выкидываем строки оглавления (Stage 36) и издательского колофона
        # (Stage 37), чтобы они не попадали в чанки и не засоряли поиск.
        body = "\n".join(
            ln
            for ln in body.split("\n")
            if not _is_toc_line(ln) and not _is_imprint_line(ln)
        )

        cleaned.append((name, body))

    return cleaned


def get_splitter():
    """Создаёт объект для разбиения текста на чанки."""
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    return RecursiveCharacterTextSplitter(
        chunk_size=config.CHUNK_SIZE,
        chunk_overlap=config.CHUNK_OVERLAP,
        separators=["\n\n\n", "\n\n", "\n", ". ", "; ", " ", ""],
    )
