def test_import_config():
    import config
    assert config.CHUNK_SIZE > 0


def test_import_prompts():
    from src.prompts import SYSTEM_PROMPT, PROMPTS
    assert SYSTEM_PROMPT
    assert "УСЛОВНЫЕ ВЫВОДЫ" in SYSTEM_PROMPT
    assert "не обещай исход" in SYSTEM_PROMPT.lower()
    assert "qa" in PROMPTS
    assert "hyde" in PROMPTS


def test_import_document_loader():
    from src.document_loader import load_file
    assert callable(load_file)


def test_import_text_processing():
    from src.text_processing import clean_sections, detect_sections, get_splitter, is_user_visible_section
    assert callable(detect_sections)
    assert callable(clean_sections)
    assert callable(get_splitter)
    assert callable(is_user_visible_section)


def test_import_chat_utils():
    from src.chat_utils import is_greeting, is_refusal, is_followup, is_correction
    assert is_greeting("привет")
    assert is_refusal("НЕТ ИНФОРМАЦИИ")
    assert is_followup("подробнее")
    assert is_correction("неправильно")
