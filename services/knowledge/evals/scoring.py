"""Scoring for eval runs - pure functions, no heavy deps.

Kept deliberately simple and deterministic so the same logic is exercised in CI
without a model or an LLM call:

* retrieval recall - fraction of ``expected_context`` substrings present in the
  retrieved context (did we even fetch the right passages?);
* keyword coverage - fraction of ``expected_keywords`` present in the answer
  (did the answer state the facts?);
* judge score    - optional 0..1 rating from an LLM judge (set by the runner).

A case passes when every *scored* dimension clears ``threshold``. Dimensions
with an empty expectation list are not scored (and don't block a pass), but a
case with no scored dimension at all counts as a fail - an eval case that
checks nothing is meaningless.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def _norm(text: str) -> str:
    """Приводит текст к виду, в котором сравниваются подстроки.

    «ё» схлопывается в «е» намеренно. В русских текстах написание через «е»
    равноправно, и модель свободно выбирает любое: на прогоне 02.09.2026
    ответ «удлиненный основной отпуск» не совпал с ожиданием «удлинённ» и был
    засчитан как провал, хотя исключение из ТК он назвал верно. Такой
    «провал» не говорит ни о качестве ответа, ни о поиске — только о том,
    какую букву выбрал автор датасета.
    """
    text = (text or "").casefold().replace("ё", "е")
    # Снимаем математическую разметку. GigaChat-2-Max оформляет числа как
    # LaTeX: "$f_{max} = 0{,}0036 \cdot p^{0{,}65}$". Значение верное, но
    # фигурные скобки вокруг запятой рвут подстроку - замер 04.09.2026: два
    # полностью правильных ответа ушли в провалы только из-за оформления, и
    # старшая модель выглядела хуже младшей. Метрика обязана мерить факт, а не
    # то, в каком синтаксисе модель его набрала.
    for ch in "${}":
        text = text.replace(ch, "")
    return text


def coverage(text: str, needles: list[str]) -> float | None:
    """Fraction of ``needles`` found as case-insensitive substrings of ``text``.
    Returns ``None`` when there are no needles (dimension not scored)."""
    if not needles:
        return None
    hay = _norm(text)
    hits = sum(1 for n in needles if _norm(n) in hay)
    return hits / len(needles)


# Слова, после которых утверждение становится его отрицанием. Нужны потому,
# что запрещённая формулировка чаще всего появляется в ответе именно тогда,
# когда ассистент её ОПРОВЕРГАЕТ, повторяя за вопросом: «нельзя утверждать,
# что ПДД полностью запрещают...». Замер 04.09.2026: два верных ответа из
# пятнадцати ушли в провалы ровно по этой причине, а проверка по голой
# подстроке отличить утверждение от опровержения не способна.
# Сильные отрицатели: ими ответ ОПРОВЕРГАЕТ формулировку, которую цитирует
# следом («нельзя утверждать, что ПДД полностью запрещают...»). Между таким
# словом и самой формулировкой обычно стоит вводная конструкция, поэтому
# смотрим широкое окно.
_STRONG_NEGATORS = (
    "нельзя", "неточн", "неверн", "ошибочн", "не является", "отсутству",
    "вопреки", "однако", "нет ",
)

# Слабое «не» отрицает СОСЕДНЕЕ слово, а не всё, что идёт дальше по строке.
# «Не рекомендовано лечение высокими дозами» отрицает рекомендацию, но слова
# «высокими дозами» при этом утверждаются - и если они пришли из инструкции к
# другому препарату, это ровно та ошибка, которую мы ловим. Замер 04.09.2026:
# при общем окне в 80 символов такой ответ получил PASS, хотя приписывал
# Нейробиону оговорку Тринейро. Поэтому для «не» окно короткое.
_WEAK_NEGATORS = ("не ",)

# Ширина окна поиска отрицания перед формулировкой. 80 - оценка по живым
# ответам для сильных отрицателей; 12 - примерно одно слово, ровно столько,
# сколько покрывает «не» в связке «не X».
_NEGATION_WINDOW = 80
_WEAK_NEGATION_WINDOW = 12


def _is_negated(hay: str, at: int) -> bool:
    """Стоит ли перед позицией ``at`` отрицание в пределах своего окна."""
    wide = hay[max(0, at - _NEGATION_WINDOW):at]
    if any(neg in wide for neg in _STRONG_NEGATORS):
        return True
    near = hay[max(0, at - _WEAK_NEGATION_WINDOW):at]
    return any(neg in near for neg in _WEAK_NEGATORS)


def present(text: str, needles: list[str]) -> list[str]:
    """Подстроки из ``needles``, которые в тексте УТВЕРЖДАЮТСЯ.

    Зеркало :func:`missing` для запрещённых формулировок. Вхождение внутри
    отрицания не считается: ответ, который цитирует ложное утверждение, чтобы
    его опровергнуть, — это как раз то поведение, которого мы добиваемся, и
    засчитывать его провалом было бы ровно наоборот.
    """
    hay = _norm(text)
    found = []
    for n in needles:
        needle = _norm(n)
        start = 0
        while True:
            at = hay.find(needle, start)
            if at == -1:
                break
            if not _is_negated(hay, at):
                found.append(n)
                break
            start = at + 1
    return found


def missing(text: str, needles: list[str]) -> list[str]:
    """Needles absent from ``text`` (the diagnostic counterpart to coverage)."""
    hay = _norm(text)
    return [n for n in needles if _norm(n) not in hay]


# Markers of the bot's "no information in the materials" answers - the canned
# ``app_services._format_no_information_message`` and the no-context fallback.
# Specific enough not to flag a partial-but-real answer as a refusal.
# Продукт отвечает отказом двумя разными текстами - для выбранного файла и для
# всех материалов (см. app_services._format_no_information_message). Знать надо
# оба: 04.09.2026 правильный отказ «не найдена в выбранном материале» пошёл в
# провалы только потому, что здесь была перечислена одна из двух формулировок.
_REFUSAL_MARKERS = (
    "не найдена в загруженных материалах",
    "не найдена в выбранном материале",
    "нет информации",
    # Отказ СВОИМИ СЛОВАМИ. Хороший отказ выглядит именно так: модель не просто
    # молчит, а называет, чего именно нет, и часто ссылается на область
    # применения документа. Замер 04.09.2026 на СП 60: два таких ответа ушли в
    # провалы - «конкретных указаний ... в представленном контексте нет» и «нет
    # конкретной информации о расчётной температуре» - хотя это лучшее
    # поведение из возможных. Харнесс знал только казённые формулировки
    # продукта и своих слов модели не понимал.
    "в контексте нет",
    "в представленном контексте нет",
    "нет конкретной информации",
    "не содержится в",
    "контекст не содержит",
    "отсутствует в контексте",
    # Отписка контентного фильтра провайдера - тоже не ответ на вопрос.
    # Считаем отказом, чтобы кейс-«дыра» не проваливался из-за фильтра, но
    # отчёт всё равно смотрим глазами: см. описание набора sp60-hvac-traps.
    "разговоры на некоторые темы временно ограничены",
    "не обладает собственным мнением и не транслирует мнение",
)


def looks_like_refusal(text: str) -> bool:
    """Did the bot decline to answer (correctly, when nothing is in the corpus)?"""
    hay = _norm(text)
    return any(m in hay for m in _REFUSAL_MARKERS)


@dataclass
class CaseScore:
    id: str
    retrieval_recall: float | None
    keyword_coverage: float | None
    judge_score: float | None
    passed: bool
    missing_keywords: list[str] = field(default_factory=list)
    missing_context: list[str] = field(default_factory=list)


def _passed(values: list[float | None], threshold: float) -> bool:
    scored = [v for v in values if v is not None]
    if not scored:
        return False
    return all(v >= threshold for v in scored)


def score_case(
    case_id: str,
    *,
    answer: str,
    context: str,
    expected_keywords: list[str],
    expected_context: list[str],
    judge_score: float | None = None,
    threshold: float = 0.6,
    expect_refusal: bool = False,
    forbidden_keywords: list[str] | None = None,
) -> CaseScore:
    # Negative case: the question is out of corpus and the bot SHOULD decline.
    # A good RAG answers "not found" instead of hallucinating - so pass on a
    # refusal, and don't score keyword/recall (there's nothing to find/state).
    if expect_refusal:
        refused = looks_like_refusal(answer)
        # Запрещённые слова проверяем И ЗДЕСЬ. Отказ - не индульгенция: ответ
        # может отказаться назвать число и при этом соврать по дороге.
        #
        # Замер 04.09.2026 на СП 60. Вопрос про рентгеновский кабинет: «В
        # документе указано, что требования свода правил РАСПРОСТРАНЯЮТСЯ на
        # сооружения ... с источниками ионизирующих излучений. Однако
        # конкретной информации ... нет». Вторая половина - честный отказ,
        # первая - переворот пункта 1.2, где сказано НЕ распространяется.
        # Такой ответ получал PASS трижды подряд: сканер видел отказ и
        # выходил, не дочитав. Проверка меряла «отказался ли», но не «не
        # соврал ли, отказываясь», - и пропускала ложь именно потому, что она
        # стояла рядом с правильным поведением.
        banned = present(answer, forbidden_keywords or [])
        notes = [] if refused else ["ожидался отказ, бот ответил"]
        notes += [f"ЗАПРЕЩЕНО в ответе: {b!r}" for b in banned]
        return CaseScore(
            id=case_id,
            retrieval_recall=None,
            keyword_coverage=None,
            judge_score=judge_score,
            passed=refused and not banned,
            missing_keywords=notes,
            missing_context=[],
        )

    recall = coverage(context, expected_context)
    cov = coverage(answer, expected_keywords)

    # Запрещённое слово перевешивает всё остальное: ответ, начинающийся с
    # неправды, не становится верным оттого, что дальше в нём нашлись нужные
    # слова. Метрики считаем и показываем как есть — они полезны для разбора, —
    # но кейс проваливаем.
    banned = present(answer, forbidden_keywords or [])
    passed = _passed([recall, cov, judge_score], threshold) and not banned

    return CaseScore(
        id=case_id,
        retrieval_recall=recall,
        keyword_coverage=cov,
        judge_score=judge_score,
        passed=passed,
        missing_keywords=(
            missing(answer, expected_keywords)
            + [f"ЗАПРЕЩЕНО в ответе: {b!r}" for b in banned]
        ),
        missing_context=missing(context, expected_context),
    )


@dataclass
class Aggregate:
    n: int
    passed: int
    pass_rate: float
    mean_recall: float | None
    mean_coverage: float | None
    mean_judge: float | None
    # Ниже - про повторные прогоны. При одном прогоне на вопрос repeats=1,
    # stability=pass_rate, unstable=0, и отчёт выглядит как раньше.
    repeats: int = 1
    # Средняя доля удачных прогонов по вопросам. Именно она двигается, когда
    # поведение становится надёжнее, а «прошёл/не прошёл» ещё не переключился.
    stability: float = 0.0
    # Вопросы, которые прошли не все прогоны и не все провалили. Их надо
    # читать глазами: это не «работает» и не «сломано», а «как повезёт».
    unstable: int = 0


def _mean(values: list[float | None]) -> float | None:
    scored = [v for v in values if v is not None]
    if not scored:
        return None
    return sum(scored) / len(scored)


def aggregate_repeated(attempts: list[list[CaseScore]]) -> Aggregate:
    """Свести прогоны, где каждый вопрос задавался несколько раз.

    Вопрос считается пройденным, только если прошли ВСЕ его прогоны. Для
    ассистента по документам это верная планка: ответ, верный пять раз из
    восьми, доверия не заслуживает - человек не знает, какой раз ему достался.

    Отдельно считается stability - средняя доля удачных прогонов. Она нужна
    потому, что «прошёл/не прошёл» слишком груб для вероятностного поведения.
    Замер 04.09.2026: правка чтения таблиц изменила один вопрос с 1 верного
    ответа из 8 на 8 из 8, а набор с одним прогоном на вопрос показал до и
    после одинаковые 19 из 26 - улучшения не было видно вообще.
    """
    if not attempts:
        return Aggregate(0, 0, 0.0, None, None, None, repeats=1)

    repeats = max(len(a) for a in attempts)
    flat = [s for group in attempts for s in group]
    per_case = [
        (sum(1 for s in group if s.passed) / len(group)) if group else 0.0
        for group in attempts
    ]
    fully = sum(1 for rate in per_case if rate == 1.0)
    n = len(attempts)
    return Aggregate(
        n=n,
        passed=fully,
        pass_rate=fully / n,
        mean_recall=_mean([s.retrieval_recall for s in flat]),
        mean_coverage=_mean([s.keyword_coverage for s in flat]),
        mean_judge=_mean([s.judge_score for s in flat]),
        repeats=repeats,
        stability=sum(per_case) / n,
        unstable=sum(1 for rate in per_case if 0.0 < rate < 1.0),
    )


def aggregate(scores: list[CaseScore]) -> Aggregate:
    n = len(scores)
    passed = sum(1 for s in scores if s.passed)
    return Aggregate(
        n=n,
        passed=passed,
        pass_rate=(passed / n) if n else 0.0,
        mean_recall=_mean([s.retrieval_recall for s in scores]),
        mean_coverage=_mean([s.keyword_coverage for s in scores]),
        mean_judge=_mean([s.judge_score for s in scores]),
    )
