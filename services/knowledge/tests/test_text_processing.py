from src.text_processing import (
    _is_imprint_line,
    _is_toc_line,
    clean_sections,
    detect_sections,
    is_user_visible_section,
)


def test_imprint_lines_are_dropped_but_prose_kept():
    # Publishing colophon (print run / "подписано к печати" / printer) pollutes
    # retrieval like a TOC; strip it. Prose that merely mentions "тираж"/"редактор"
    # without the imprint phrasing must survive.
    assert _is_imprint_line("Подписано к печати 20.12.2023")
    assert _is_imprint_line("Объем 11,25 печ. л. Тираж 29 экз. Заказ 1554")
    assert _is_imprint_line("Отпечатано в СПбГУТ")
    assert not _is_imprint_line("Книга вышла большим тиражом и стала популярной.")
    assert not _is_imprint_line("Редактор обсудил с автором сюжет романа.")

    body = (
        "ИСТОРИЯ НАУКИ И ТЕХНИКИ\n"
        "Телеграф был изобретён в XIX веке.\n"
        "Подписано к печати 20.12.2023\n"
        "Тираж 29 экз. Отпечатано в СПбГУТ"
    )
    (_name, cleaned), = clean_sections([("title", body)])
    assert "Телеграф был изобретён" in cleaned
    assert "Подписано к печати" not in cleaned and "Отпечатано" not in cleaned


def test_toc_lines_are_dropped_but_prose_kept():
    # Table-of-contents entries (dot leader + page number) pollute retrieval;
    # they must be stripped, while normal prose (incl. ellipsis / "главу 5") stays.
    assert _is_toc_line("§ 6. Дифференциальные уравнения . . . . . 54")
    assert _is_toc_line("Преобразование Лапласа..........76")
    assert not _is_toc_line("Решим дифференциальное уравнение методом Лапласа.")
    assert not _is_toc_line("См. также главу 5 и формулу 12.")
    assert not _is_toc_line("И так далее... остановимся на главном.")

    body = (
        "§ 6. Дифференциальные уравнения . . . . . 54\n"
        "Это содержательный абзац про метод Лапласа.\n"
        "§ 8. Дискретное преобразование . . . . . 60"
    )
    (_name, cleaned), = clean_sections([("§ 6", body)])
    assert "содержательный абзац" in cleaned
    assert "54" not in cleaned and "60" not in cleaned


def test_detect_sections_by_headers():
    text = """
ВВЕДЕНИЕ

Это первый раздел текста.

ГЛАВА 1

Это текст первой главы.
"""

    sections = detect_sections(text)

    assert len(sections) >= 2
    section_names = [name for name, _ in sections]

    assert "ВВЕДЕНИЕ" in section_names
    assert "ГЛАВА 1" in section_names


def test_detect_sections_accepts_number_only_chapter_headers():
    text = """
ГЛАВА 1

Первый раздел.

ГЛАВА 2.

Второй раздел.
"""

    sections = detect_sections(text)

    assert [name for name, _ in sections] == ["ГЛАВА 1", "ГЛАВА 2."]


def test_detect_sections_ignores_pdf_caption_and_bibliography_noise():
    text = """
§ 10. Советская экономика после войны

Основной текст раздела.

(КАМАЗ, г. Набережные Челны), Вол -

100. М.В. Ломоносов. Российская грамматика / [соч.] Михаила Ломоносова. – СПб.,

Июль 2023 г. Автор фото: Мария Девахина. © РИА Новости».

11. Почему СССР победил в Великой Отечественной вой не? Каковы были цена

Продолжение основного текста.

§ 11. Политические реформы

Следующий раздел.
"""

    sections = detect_sections(text)
    section_names = [name for name, _ in sections]

    assert section_names == [
        "§ 10. Советская экономика после войны",
        "§ 11. Политические реформы",
    ]


def test_detect_sections_keeps_real_uppercase_topic_headers():
    text = """
РОССИЯ 1990-Х ГОДОВ

Текст раздела.

РОССИЙСКАЯ ФЕДЕРАЦИЯ В НАЧАЛЕ XXI ВЕКА

Следующий текст.
"""

    sections = detect_sections(text)

    assert [name for name, _ in sections] == [
        "РОССИЯ 1990-Х ГОДОВ",
        "РОССИЙСКАЯ ФЕДЕРАЦИЯ В НАЧАЛЕ XXI ВЕКА",
    ]


def test_detect_sections_does_not_treat_acronym_heavy_sentences_as_headers():
    text = """
§ 12. Великая Отечественная война

Почему СССР и РККА смогли победить в Великой Отечественной войне?

В тексте встречаются КПСС, СНК, ВЦИК и другие сокращения, но это обычный абзац.

§ 13. Послевоенное восстановление

Следующий раздел.
"""

    sections = detect_sections(text)

    assert [name for name, _ in sections] == [
        "§ 12. Великая Отечественная война",
        "§ 13. Послевоенное восстановление",
    ]


def test_user_visible_section_accepts_real_subsection_headers():
    assert is_user_visible_section("4.5. Широкополосные беспроводные сети")
    assert is_user_visible_section("4.5.1. Сравнение стандарта 802.16 с 802.11 и 3G")


def test_user_visible_section_rejects_formula_and_bibliography_noise():
    assert not is_user_visible_section("fi(B,C,D) = (B AND C) OR (NOT B AND D) (0<i<19)")
    assert not is_user_visible_section("RTTVAR = βRTTVAR + (1 - β)|SRTT - R|.")
    assert not is_user_visible_section("HUNTER, D., RAFTER, J., FAWCETT, J., VAN DER LIST, E., AYERS, D.")
    assert not is_user_visible_section("QAM, QPSK, RED, RFC, RPC, RSA, RSVP, RTP, SSL, TCP, TDM, UDP, URL, UTP,")


def test_clean_sections_removes_extra_spaces_and_newlines():
    sections = [
        ("Раздел", "Текст   с   лишними    пробелами.\n\n\n\nНовая строка.")
    ]

    cleaned = clean_sections(sections)

    assert cleaned[0][1] == "Текст с лишними пробелами.\n\nНовая строка."
