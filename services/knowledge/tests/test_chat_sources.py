"""Chat source grouping + passage snippet (Stage 56). Pure function, CI-safe."""

from src.app_services import _clean_passage, _group_chat_sources


def test_group_sources_carries_snippet():
    raw = [
        {"source_file": "lec.pdf", "section": "Глава 1", "score": 0.9, "text": "Энтропия - мера хаоса."},
        {"source_file": "lec.pdf", "section": "Глава 2", "score": 0.7, "text": "Второй закон термодинамики."},
    ]
    out = _group_chat_sources(raw, selected_file="Все файлы")
    by_section = {s.section: s for s in out}
    assert by_section["Глава 1"].snippet == "Энтропия - мера хаоса."
    assert by_section["Глава 2"].snippet == "Второй закон термодинамики."


def test_group_sources_preserves_distinct_passages_from_same_section():
    # A lower-ranked passage may contain a condition absent from the first.
    raw = [
        {"source_file": "x.pdf", "section": "A", "score": 0.3, "text": "слабый фрагмент"},
        {"source_file": "x.pdf", "section": "A", "score": 0.8, "text": "сильный фрагмент"},
    ]
    out = _group_chat_sources(raw, selected_file="Все файлы")
    assert len(out) == 2
    assert out[0].snippet == "сильный фрагмент"
    assert out[0].score == 0.8
    assert out[1].snippet == "слабый фрагмент"


def test_group_sources_preserves_page_and_adds_it_to_label():
    out = _group_chat_sources(
        [{
            "source_file": "manual.pdf",
            "section": "Безопасность",
            "page_start": 41,
            "page_end": 42,
            "score": 0.9,
            "text": "Перед обслуживанием отключите питание.",
        }],
        selected_file="Все файлы",
    )

    assert out[0].page_start == 41
    assert out[0].page_end == 42
    assert "стр. 41–42" in out[0].label


def test_group_sources_keeps_safety_instruction_after_500_characters():
    passage = "Background information. " * 30 + "DANGER SIGNS: call an ambulance NOW."
    out = _group_chat_sources(
        [{"source_file": "x.pdf", "section": "A", "score": 0.5, "text": passage}],
        selected_file="Все файлы",
    )
    assert out[0].snippet == passage
    assert out[0].snippet.endswith("call an ambulance NOW.")


def test_group_sources_keeps_evidence_beyond_fourth_result():
    raw = [
        {"source_file": "manual.pdf", "section": str(i), "score": 1 / (i + 1),
         "text": f"Condition {i}", "page_start": i + 1, "page_end": i + 1}
        for i in range(12)
    ]
    out = _group_chat_sources(raw, selected_file="Все файлы")
    assert len(out) == 12
    assert out[-1].snippet == "Condition 11"
    assert out[-1].page_start == 12


def test_group_sources_deduplicates_only_identical_passages():
    raw = [
        {"source_file": "x.pdf", "section": "A", "score": score, "text": "Same passage"}
        for score in (0.3, 0.8)
    ]
    out = _group_chat_sources(raw, selected_file="Все файлы")
    assert len(out) == 1
    assert out[0].score == 0.8


# --- Passage cleanup: PDF/OCR hyphenation + line reflow ---------------------


def test_clean_passage_rejoins_soft_hyphenated_words():
    # PDF extractors keep the page's hyphenation: a soft hyphen (U+00AD) plus
    # the line break splitting a word across two lines.
    raw = "уравнения с веществен\u00ad\nными коэффициентами, но не доста\u00ad\nточное условие"
    assert _clean_passage(raw) == "уравнения с вещественными коэффициентами, но не достаточное условие"


def test_clean_passage_rejoins_hard_hyphen_at_line_break():
    assert _clean_passage("операци-\nонное исчисление") == "операционное исчисление"


def test_clean_passage_reflows_layout_newlines_into_prose():
    raw = "Большое практическое значение\nимеют необходимые условия\nтого, чтобы все корни"
    assert _clean_passage(raw) == "Большое практическое значение имеют необходимые условия того, чтобы все корни"


def test_clean_passage_leaves_ocr_word_splits_alone():
    # "Гурви ца" is an OCR-invented space *inside* a word. Guessing which spaces
    # to close would corrupt correct text, so we deliberately don't touch it.
    assert _clean_passage("критерий Рауса-Гурви ца") == "критерий Рауса-Гурви ца"


def test_clean_passage_handles_empty():
    assert _clean_passage("") == ""
    assert _clean_passage(None) == ""


def test_group_sources_cleans_the_snippet():
    out = _group_chat_sources(
        [{"source_file": "x.pdf", "section": "A", "score": 0.5, "text": "поло\u00ad\nжительность\nвсех корней"}],
        selected_file="Все файлы",
    )
    assert out[0].snippet == "положительность всех корней"
