"""
chat_utils.py - вспомогательные функции для обработки сообщений и истории чата.
"""

REFUSAL_PHRASES = [
    "нет информации", "не упоминается", "не содержит",
    "не могу найти", "отсутствует", "нет данных",
    "no information", "not mentioned",
]

FOLLOWUP_PHRASES = [
    "точно", "уверен", "правда", "докажи", "обоснуй",
    "подробнее", "аргументы", "аргумент", "объясни", "почему",
    "зачем", "пример", "примеры", "поясни", "разъясни", "расскажи подробнее",
    "как так", "серьёзно", "не понял", "уточни", "ещё",
    "а что", "а как", "а почему", "а зачем", "расскажи ещё",
    "продолжи", "дальше",
]

CORRECTION_PHRASES = [
    "нет,", "неправильно", "неверно", "ошибка", "ты ошибся",
    "правильный ответ", "на самом деле", "не так", "неточно",
    "ты не прав", "это неправда", "некорректно", "нет это",
    "ответ неверный", "ответ неправильный", "нет правильный",
    "все-таки", "всё-таки", "однако нет", "а вот нет",
]

GREETING_PHRASES = [
    "привет", "здравствуй", "добрый", "hi", "hello",
]


def extract_content(msg):
    """Извлечь текст из сообщения истории чата."""
    content = msg.get("content", "") if isinstance(msg, dict) else str(msg)

    if isinstance(content, list):
        content = " ".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
        )

    return str(content).strip()


def is_greeting(text):
    """Определяет короткое приветствие."""
    lower = text.lower().strip()
    words = lower.replace(",", " ").replace("!", " ").replace(".", " ").split()

    if len(words) > 3:
        return False

    return any(word in GREETING_PHRASES for word in words)


# Обороты, которыми ответ противопоставляет вопросу то, что РЕАЛЬНО написано в
# документе. В чистом отказе их не бывает: отказ сообщает об отсутствии и на
# этом заканчивается, а здесь дальше идёт факт из контекста.
#
# Зачем нужны. На ложную предпосылку («почему Тринейро принимают до еды»)
# модель отвечает по существу и верно - «наоборот, указано, что после еды», -
# но начинает с оборота отсутствия, потому что буквального ответа на «почему»
# в документе нет. Замер 04.09.2026: три таких ответа из восемнадцати
# распознавались как отказ, и поправка - единственное, ради чего вопрос и
# задавался, - заменялась шаблоном «информация не найдена». Человек оставался
# при своём неверном представлении, хотя система его уже опровергла.
TRAILING_PUNCTUATION = " .:;" + chr(10) + chr(13) + chr(9) + chr(0x2014) + chr(0x2013) + "-"

CONTRADICTION_PHRASES = [
    "наоборот", "напротив", "вместо этого", "сказано иначе", "указано иначе",
]


def states_the_opposite(text):
    """Ответ не просто отказывает, а называет фактическое из документа."""
    lower = text.lower()
    return any(phrase in lower for phrase in CONTRADICTION_PHRASES)


# Отписки контентного фильтра провайдера. Это НЕ ответ модели на вопрос: текст
# приходит вместо ответа, когда фильтр сработал на содержимое запроса.
#
# Зачем нужны. Замер 04.09.2026 на СП 60.13330.2020 (нормативный документ
# Минстроя): вопрос «какая удельная энтальпия наружного воздуха для Москвы» на
# фрагменте с городами 1-17 отвечается верно - 57,8 кДж/кг, а на фрагменте с
# городами 18-36 фильтр GigaChat отказывает целиком. Разница между фрагментами
# только в названиях городов; вопрос, модель и настройки те же. То есть фильтр
# срабатывает на СОДЕРЖИМОЕ документа пользователя, а не на его вопрос, и
# компания на это повлиять не может: ни документ, ни фильтр не её.
#
# Без распознавания эта отписка уходила пользователю КАК ОТВЕТ - с четырьмя
# ссылками на документ и меткой уверенности «high» (она считается по числу
# источников). Человек видит реквизиты своего документа под текстом, который к
# вопросу не относится.
PROVIDER_FILTER_PHRASES = [
    "разговоры на некоторые темы временно ограничены",
    "разговоры на чувствительные темы могут быть ограничены",
    "ответы на вопросы, связанные с чувствительными темами",
    "не обладает собственным мнением и не транслирует мнение своих разработчиков",
    "во избежание неправильного толкования",
]


def is_provider_filter(text):
    """Ответ подменён отпиской контентного фильтра провайдера."""
    lower = (text or "").lower()
    return any(phrase in lower for phrase in PROVIDER_FILTER_PHRASES)


def is_refusal(text):
    """Проверяет, сказала ли LLM, что информации нет."""
    lower = text.lower().strip()

    if not lower:
        return True

    # Поправка важнее отказа: см. CONTRADICTION_PHRASES.
    if states_the_opposite(lower):
        return False

    if len(lower) < 150:
        return any(phrase in lower for phrase in REFUSAL_PHRASES)

    return any(phrase in lower[:100] for phrase in REFUSAL_PHRASES)


def strip_refusal_prefix(text):
    """Убирает ведущее «НЕТ ИНФОРМАЦИИ», если дальше идёт содержательный ответ.

    Модель иногда сначала честно отвечает «нет» на буквальную формулировку
    вопроса и только потом сообщает, что написано в документе на самом деле.
    Смысл несёт вторая часть, а первая строка в интерфейсе читается как отказ -
    человек закрывает ответ, не дочитав до поправки. Вызывается только там, где
    ответ уже признан НЕ отказом (см. is_refusal), поэтому настоящие отказы
    остаются нетронутыми.
    """
    stripped = text.lstrip()
    lowered = stripped.lower()
    for marker in ("нет информации", "no information"):
        if not lowered.startswith(marker):
            continue
        rest = stripped[len(marker):].lstrip(TRAILING_PUNCTUATION)
        # Пустой хвост означает, что это всё-таки чистый отказ, - не трогаем.
        if rest.strip():
            return rest
    return text


def is_correction(text):
    """Определяет, исправляет ли пользователь предыдущий ответ."""
    lower = text.lower().strip()

    if len(lower.split()) > 20:
        return False

    return any(
        lower.startswith(phrase) or f" {phrase} " in lower
        for phrase in CORRECTION_PHRASES
    )


def is_followup(text):
    """Определяет уточняющий вопрос к предыдущему ответу."""
    lower = text.lower().strip()
    words = lower.split()

    if not lower:
        return False

    if len(words) > 12:
        return False

    exact_followups = {
        "подробнее",
        "поясни",
        "объясни",
        "уточни",
        "дальше",
        "продолжи",
        "ещё",
        "еще",
        "пример",
        "примеры",
        "почему",
        "зачем",
        "не понял",
        "непонятно",
    }

    if lower in exact_followups:
        return True

    followup_starts = [
        "а как",
        "а почему",
        "а зачем",
        "а что",
        "а если",
        "как так",
        "и что",
        "то есть",
    ]

    if any(lower.startswith(prefix) for prefix in followup_starts):
        return True

    # Важно: "Что такое TCP?" — это новый самостоятельный вопрос, а не уточнение.
    return False


def history_to_context(history, n_last=4):
    """Превращает историю чата в текст для промпта."""
    if not history or len(history) < 2:
        return ""

    recent = history[-n_last * 2:] if len(history) > n_last * 2 else history
    lines = []

    for msg in recent:
        role = "Пользователь" if msg.get("role") == "user" else "Ассистент"
        content = extract_content(msg)

        if content:
            lines.append(f"{role}: {content}")

    return "\n".join(lines)


def get_last_qa(history):
    """Извлекает последний вопрос и ответ из истории."""
    last_answer, last_question = "", ""

    for msg in reversed(history):
        role = msg.get("role", "")
        content = extract_content(msg)

        if role == "assistant" and not last_answer:
            last_answer = content
        elif role == "user" and not last_question:
            last_question = content
            break

    return last_question, last_answer