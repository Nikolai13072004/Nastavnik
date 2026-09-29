"""Ремонт текстового слоя сканов (гомоглифы + переносы).

Все «плохие» строки ниже — настоящие фрагменты из проиндексированного учебника
Краснова «Операционное исчисление» (скан 2003 г.), а не выдуманные примеры.
Чистые функции, моделей не грузят — CI прогоняет их целиком.
"""

from src.text_repair import (
    join_hyphenated_linebreaks,
    repair_homoglyphs,
    repair_scanned_text,
)


# --- Подмена букв ----------------------------------------------------------


def test_latin_n_becomes_cyrillic_pe():
    # Самая частая поломка корпуса: 411 вхождений на 245 КБ.
    assert repair_homoglyphs("nри nолучим nорядка") == "при получим порядка"


def test_latin_r_becomes_cyrillic_ge():
    assert repair_homoglyphs("rде этоrо друrой") == "где этого другой"


def test_true_homoglyphs_are_normalized():
    # Латинские a/c/e/o/p/x/y неотличимы от кириллических.
    assert repair_homoglyphs("словo") == "слово"


def test_repairs_word_with_several_substitutions():
    assert repair_homoglyphs("nолуnлоскости") == "полуплоскости"
    assert repair_homoglyphs("асимnтотически") == "асимптотически"


def test_override_wins_where_the_general_map_would_guess_wrong():
    # В этом корпусе `n` — почти всегда «п», но «дnя» это «для», не «дпя».
    assert repair_homoglyphs("дnя всех t") == "для всех t"


# --- Чего трогать нельзя ---------------------------------------------------


def test_latin_math_identifiers_survive():
    # Ради читаемости формул: ни одно из этих слов не должно измениться.
    formula = "sin cos exp tg ch sh Re dt f(t) F(p) max"
    assert repair_homoglyphs(formula) == formula


def test_single_cyrillic_letter_next_to_latin_is_left_alone():
    # Одиночная кириллическая буква рядом с латиницей - это формула, а не слово.
    assert repair_homoglyphs("xt") == "xt"


def test_word_with_unknown_latin_letter_is_not_touched():
    # `w` даёт неоднозначный результат ("Коwи"->Коши, но "устойчивwм"->устойчивым),
    # поэтому в карту не входит и слово остаётся как есть - лучше видимый мусор,
    # чем правдоподобная подмена.
    assert repair_homoglyphs("Коwи") == "Коwи"


def test_pure_russian_text_is_unchanged():
    text = "Функция-оригинал должна быть локально интегрируемой."
    assert repair_homoglyphs(text) == text


# --- Переносы --------------------------------------------------------------


def test_soft_hyphen_at_line_break_joins_the_word():
    assert join_hyphenated_linebreaks("веществен­\nными") == "вещественными"


def test_stray_soft_hyphen_without_a_break_is_dropped():
    assert join_hyphenated_linebreaks("доста­точное") == "достаточное"


def test_hard_hyphen_joins_only_across_a_line_break():
    assert join_hyphenated_linebreaks("операци-\nонное") == "операционное"
    # Внутри строки дефис может быть настоящим - его не трогаем.
    assert join_hyphenated_linebreaks("Рауса-Гурвица") == "Рауса-Гурвица"


def test_line_structure_is_preserved():
    # Ремонт при извлечении не должен схлопывать абзацы: на строках держится
    # разбиение на разделы и чанки.
    assert join_hyphenated_linebreaks("Первая строка\nВторая строка").count("\n") == 1


# --- Всё вместе ------------------------------------------------------------


def test_full_repair_on_a_real_passage():
    raw = (
        "Положительность всех коэффициентов -необходимое, но не доста­\n"
        "точное условие для тоrо, чтобы все корни уравнения были расnоложены слева."
    )
    fixed = repair_scanned_text(raw)
    assert "достаточное" in fixed
    assert "того" in fixed
    assert "расположены" in fixed


def test_repair_handles_empty_input():
    assert repair_scanned_text("") == ""
    assert repair_scanned_text(None) == ""


# --- Устойчивость пути к материалу ------------------------------------------
# Document.stored_path абсолютный, поэтому протухает при переносе проекта или
# переезде БД между Docker и локальной машиной. Файлы при этом на месте.


def test_resolve_stored_path_recovers_a_moved_install(tmp_path, monkeypatch):
    import config
    from src import storage

    workspace = "ws-1"
    monkeypatch.setattr(config, "DOCS_DIR", str(tmp_path / "docs"))
    docs_dir = tmp_path / "docs" / workspace
    docs_dir.mkdir(parents=True)
    real = docs_dir / "doc-id__lecture.pdf"
    real.write_bytes(b"%PDF-1.4")

    # Windows-путь из БД, поднятой на другой машине. Разбирать его должен уметь
    # и Linux (база с Windows -> Docker) - os.path.basename там backslash не
    # считает разделителем и вернул бы путь целиком.
    stale = r"C:\старое\место\docs\ws-1\doc-id__lecture.pdf"
    assert storage.resolve_stored_path(stale, workspace) == str(real)

    # И симметрично: posix-путь, который перестал существовать (Docker -> Windows).
    stale_posix = "/app/docs/ws-1/doc-id__lecture.pdf"
    assert storage.resolve_stored_path(stale_posix, workspace) == str(real)


def test_resolve_stored_path_keeps_a_working_path(tmp_path, monkeypatch):
    import config
    from src import storage

    monkeypatch.setattr(config, "DOCS_DIR", str(tmp_path / "docs"))
    docs_dir = tmp_path / "docs" / "ws-1"
    docs_dir.mkdir(parents=True)
    real = docs_dir / "doc-id__lecture.pdf"
    real.write_bytes(b"%PDF-1.4")

    assert storage.resolve_stored_path(str(real), "ws-1") == str(real)


def test_resolve_stored_path_returns_original_when_nothing_found(tmp_path, monkeypatch):
    # Файла нет нигде -> отдаём исходный путь, чтобы вызывающий код сказал
    # "файл отсутствует", а не подставил несуществующее имя молча.
    import config
    from src import storage

    monkeypatch.setattr(config, "DOCS_DIR", str(tmp_path / "docs"))
    missing = str(tmp_path / "nowhere" / "doc-id__gone.pdf")
    assert storage.resolve_stored_path(missing, "ws-1") == missing


# --- Колонтитулы ----------------------------------------------------------
#
# Порог здесь не «повторяется N раз», а «есть на половине страниц». Замер
# 04.09.2026 на СП 60.13330.2020: настоящий текст повторяется до 18 раз
# (строки таблиц, типовые формулировки), а колонтитул - 130-134. Любой порог
# около десятка выкосил бы содержание, поэтому тесты ниже проверяют ОБА
# направления: мусор уходит, повторяющееся содержание остаётся.

def _page(n):
    """Страница с реалистичной долей оформления: три служебные строки на
    полтора десятка содержательных. На СП 60 это 14% строк - если сделать
    больше, сработает предохранитель _FURNITURE_MAX_REMOVED_SHARE, и чистка
    правильно откажется трогать такой документ."""
    body = [f"Пункт 7.{n}.{i}. Содержательное требование номер {n}." for i in range(1, 15)]
    return [
        "КонсультантПлюс",
        f"надежная правовая поддержка www.consultant.ru Страница {n} из 60",
        "Документ предоставлен КонсультантПлюс",
        *body,
        f"{n} Астрахань 63,6 11,7",
    ]


def _doc(pages=60):
    lines = []
    for n in range(1, pages + 1):
        lines.extend(_page(n))
    return "\n".join(lines)


def test_page_furniture_is_removed():
    from src.text_repair import strip_page_furniture

    out = strip_page_furniture(_doc())
    assert "КонсультантПлюс" not in out
    assert "надежная правовая поддержка" not in out


def test_content_survives_the_stripper():
    from src.text_repair import strip_page_furniture

    out = strip_page_furniture(_doc())
    assert "Содержательное требование номер 7." in out
    assert "Пункт 7.7.14." in out
    assert "7 Астрахань 63,6 11,7" in out


def test_repeated_real_text_is_not_furniture():
    """Строка, повторённая заметно реже, чем есть страниц, — это содержание."""
    from src.text_repair import strip_page_furniture

    lines = []
    for n in range(1, 61):
        lines.append(f"Пункт {n}. Уникальный текст {n}.")
        if n % 4 == 0:  # 15 повторов на 60 страниц - это не колонтитул
            lines.append("температуре теплоносителя не более 95 °C.")
    text = "\n".join(lines)
    assert "температуре теплоносителя не более 95 °C." in strip_page_furniture(text)


def test_short_document_is_left_alone():
    from src.text_repair import strip_page_furniture

    text = "Одна строка.\nДругая строка.\nОдна строка."
    assert strip_page_furniture(text) == text


def test_pdf_furniture_uses_document_frequency_not_single_dense_page():
    from src.text_repair import strip_pdf_page_furniture

    pages = []
    for number in range(1, 45):
        lines = ["Repeated document header"]
        lines += ["Unique requirement " + "".join(chr(97 + int(ch)) for ch in str(number * 100 + i)) for i in range(40)]
        if number == 11:
            lines += [f"dated April {i}, 2006, transmitted to Congress by the Chief Justice" for i in range(1, 10)]
        pages.append((number, "\n".join(lines)))
    cleaned = strip_pdf_page_furniture(pages)
    assert [n for n, _ in cleaned] == list(range(1, 45))
    assert "Repeated document header" not in cleaned[10][1]
    assert "dated April 1, 2006" in cleaned[10][1]
    assert "dated April 9, 2006" in cleaned[10][1]
