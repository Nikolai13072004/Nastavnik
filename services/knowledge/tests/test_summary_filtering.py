from src.knowledge_base import KnowledgeBase


def make_kb():
    return KnowledgeBase.__new__(KnowledgeBase)


def test_summary_topic_score_rejects_chunks_after_requested_year_range():
    kb = make_kb()

    score = kb._topic_lexical_score(
        "После 2003 года произошли события 2008 и 2012 годов. "
        "Материал описывает реформы 2010-х годов и не относится к XX веку.",
        "Россия после Николая II до 2000 года",
    )

    assert score <= 0


def test_summary_topic_score_keeps_chunks_inside_requested_year_range():
    kb = make_kb()

    score = kb._topic_lexical_score(
        "В 1917 году произошла революция. Затем началась Гражданская война, "
        "в 1920-е годы проводился НЭП, а в 1991 году произошел распад СССР.",
        "Россия после Николая II до 2000 года",
    )

    assert score > 0


def test_summary_topic_score_rejects_mostly_after_range_mixed_chunks():
    kb = make_kb()

    score = kb._topic_lexical_score(
        "В 1991 году произошел распад СССР. Далее материал переходит к событиям "
        "2003, 2008, 2012 и 2014 годов, реформам 2010-х и современной политике.",
        "Россия после Николая II до 2000 года",
    )

    assert score <= 0


def test_summary_noise_filter_rejects_image_captions_and_source_lists():
    kb = make_kb()

    assert kb._is_noise_summary_chunk(
        "110. Вооруженная революционная охрана у здания Петроградского совета. "
        "Цит. по: История России: В 20 т. / Ин-т российской истории РАН. "
        "Автор фото: РИА Новости. " + "Описание изображения и ссылка на архив. " * 8
    )
    assert kb._is_noise_summary_chunk(
        "137. Плакат «Да здравствует коллективизация деревни». "
        "Список иллюстраций и источников. М., 2024. "
        + "Библиографическое описание изображения. " * 8
    )


def test_summary_noise_filter_rejects_caption_after_section_label():
    kb = make_kb()

    assert kb._is_noise_summary_chunk(
        "[СПИСОК ИЛЛЮСТРАЦИЙ]\n"
        "110. Вооруженная революционная охрана у здания Петроградского совета. "
        "111. А.Ф. Керенский. Портрет. "
        "112. В.И. Ленин. Цит. по: История России: В 20 т. "
        + "Автор фото: архив РИА Новости. " * 8
    )


def test_topic_lexical_score_prefers_exact_topic_heading():
    kb = make_kb()

    exact = kb._topic_lexical_score(
        "4.5. Широкополосные беспроводные сети. WiMAX и стандарт 802.16.",
        "Широкополосные беспроводные сети",
    )
    broad = kb._topic_lexical_score(
        "Беспроводные локальные сети используют стандарт IEEE 802.11.",
        "Широкополосные беспроводные сети",
    )

    assert exact > broad + 20
