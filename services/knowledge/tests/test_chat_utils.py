from src.chat_utils import (
    is_greeting,
    is_refusal,
    is_followup,
    is_correction,
    history_to_context,
    get_last_qa,
)


def test_greeting_detection():
    assert is_greeting("привет")
    assert is_greeting("добрый день")
    assert not is_greeting("привет, расскажи подробно про главу")


def test_refusal_detection():
    assert is_refusal("НЕТ ИНФОРМАЦИИ")
    assert is_refusal("В тексте нет данных по этому вопросу.")
    assert not is_refusal("В тексте говорится о главном герое.")


def test_followup_detection():
    assert is_followup("подробнее")
    assert is_followup("почему")
    assert is_followup("а как это работает")
    assert not is_followup("Что такое TCP?")
    assert not is_followup("Что такое DNS?")
    assert not is_followup("Что такое MAC-адрес?")
    assert not is_followup("Расскажи мне полностью содержание первой главы произведения")


def test_correction_detection():
    assert is_correction("неправильно")
    assert is_correction("нет, это не так")
    assert not is_correction("Расскажи подробно про неправильное поведение героя в тексте")


def test_history_helpers():
    history = [
        {"role": "user", "content": "Что такое RAG?"},
        {"role": "assistant", "content": "RAG — это подход с поиском по источникам."},
    ]

    ctx = history_to_context(history)
    question, answer = get_last_qa(history)

    assert "Пользователь: Что такое RAG?" in ctx
    assert "Ассистент: RAG" in ctx
    assert question == "Что такое RAG?"
    assert answer.startswith("RAG")

# --- Поправка на ложную предпосылку не должна выглядеть как отказ ----------
#
# На вопрос с ложной предпосылкой модель отвечает по существу («наоборот,
# указано, что после еды»), но начинает с оборота отсутствия, потому что
# буквального ответа в документе нет. Если считать это отказом, поправка -
# единственное, ради чего вопрос и задавался, - заменяется шаблоном
# «информация не найдена», и человек остаётся при своём неверном
# представлении.

CORRECTION = (
    "НЕТ ИНФОРМАЦИИ\n\nКонтекст не содержит сведений о том, почему Тринейро "
    "принимают до еды. Наоборот, указано, что препарат принимается после еды."
)


def test_correction_is_not_a_refusal():
    from src.chat_utils import is_refusal

    assert not is_refusal(CORRECTION)


def test_plain_refusal_is_still_a_refusal():
    from src.chat_utils import is_refusal

    assert is_refusal("НЕТ ИНФОРМАЦИИ")
    assert is_refusal("В контексте нет информации по этому вопросу.")
    assert is_refusal("")


def test_refusal_prefix_is_stripped_only_when_something_follows():
    from src.chat_utils import strip_refusal_prefix

    assert strip_refusal_prefix(CORRECTION).startswith("Контекст не содержит")
    assert strip_refusal_prefix("НЕТ ИНФОРМАЦИИ") == "НЕТ ИНФОРМАЦИИ"
    assert strip_refusal_prefix("НЕТ ИНФОРМАЦИИ.") == "НЕТ ИНФОРМАЦИИ."
    assert strip_refusal_prefix("Доза 2 мл.") == "Доза 2 мл."


# --- Отписка контентного фильтра провайдера — не ответ --------------------
#
# Замер 04.09.2026 на СП 60.13330.2020: фильтр GigaChat срабатывает на
# содержимое документа (фрагмент с частью таблицы городов), а не на вопрос.
# Отписка уходила пользователю как ответ, со ссылками на его же документ.

FILTER_REPLY = (
    "Как и любая языковая модель, GigaChat не обладает собственным мнением и не "
    "транслирует мнение своих разработчиков. Ответ сгенерирован нейросетевой "
    "моделью... Во избежание неправильного толкования, разговоры на некоторые "
    "темы временно ограничены."
)


def test_provider_filter_reply_is_detected():
    from src.chat_utils import is_provider_filter

    assert is_provider_filter(FILTER_REPLY)


def test_real_answers_are_not_mistaken_for_a_filter_reply():
    from src.chat_utils import is_provider_filter

    assert not is_provider_filter("Удельная энтальпия для Москвы — 57,8 кДж/кг.")
    assert not is_provider_filter("Информация по данному вопросу не найдена.")
    assert not is_provider_filter("")
