"""Eval dataset schema + loader.

Pure stdlib (json + dataclasses + pathlib) so importing this module never pulls
in the embedding model or the LLM client. The CI-safe test imports it directly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# Sentinels the UI uses for "search across everything". The runner maps any of
# these to the KB's ``file_filter="all"``.
ALL_FILES = ("Все файлы", "Все материалы", "all", "")

_CASE_KEYS = {
    "id",
    "question",
    "material",
    "answer_mode",
    "expected_keywords",
    "expected_context",
    "expect_refusal",
    "forbidden_keywords",
    "category",
    "critical",
}


@dataclass
class EvalCase:
    """One question + the facts a good answer / retrieval should surface.

    ``expected_keywords`` are substrings (case-insensitive) that a correct
    *answer* must contain; ``expected_context`` are substrings the *retrieved
    context* must contain (retrieval recall). Either list may be empty, in which
    case that dimension is simply not scored for the case.
    """

    id: str
    question: str
    material: str = "Все файлы"
    answer_mode: str = "Обычный"
    expected_keywords: list[str] = field(default_factory=list)
    expected_context: list[str] = field(default_factory=list)
    # Negative case: the answer should be a refusal ("not in the materials").
    # When true, keyword/recall are not scored - passing means the bot declined.
    expect_refusal: bool = False
    # Подстроки, которых в верном ответе быть НЕ должно. Нужны там, где проверка
    # «упомянуты ли нужные слова» бессильна: на вопрос с ложной предпосылкой
    # («почему ПДД полностью запрещают обгон») ассистент может подтвердить ложь
    # первой фразой, а следом процитировать условия - и все ожидаемые слова
    # окажутся на месте. Замер 04.09.2026: такой ответ получил PASS с полным
    # покрытием, хотя открывался прямой неправдой. Найденное запрещённое слово
    # роняет кейс независимо от остальных метрик.
    #
    # Правило составления: сюда идут только выдумки и формулировки,
    # заимствованные из ДРУГОГО документа. Ни в коем случае не слова самого
    # вопроса: верный ответ на ложную предпосылку обязан её процитировать,
    # чтобы опровергнуть, и такой запрет валит именно правильные ответы.
    # Замер 04.09.2026: два верных ответа из восемнадцати ушли в провалы
    # ровно потому, что автор набора запретил слова из вопроса.
    forbidden_keywords: list[str] = field(default_factory=list)
    category: str = "factual"
    critical: bool = False


@dataclass
class EvalDataset:
    name: str
    description: str
    # Absolute paths to material files the runner ingests into the eval space.
    materials: list[str]
    cases: list[EvalCase]


def _coerce_case(raw: dict, index: int) -> EvalCase:
    if not isinstance(raw, dict):
        raise ValueError(f"case #{index} must be an object, got {type(raw).__name__}")
    unknown = set(raw) - _CASE_KEYS
    if unknown:
        raise ValueError(f"case #{index} has unknown keys: {sorted(unknown)}")
    case_id = str(raw.get("id") or "").strip()
    question = str(raw.get("question") or "").strip()
    if not case_id:
        raise ValueError(f"case #{index} is missing 'id'")
    if not question:
        raise ValueError(f"case '{case_id}' is missing 'question'")

    def string_list(key: str) -> list[str]:
        value = raw.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            raise ValueError(f"case '{case_id}' field '{key}' must be a list of non-empty strings")
        return value

    for key in ("expect_refusal", "critical"):
        if key in raw and not isinstance(raw[key], bool):
            raise ValueError(f"case '{case_id}' field '{key}' must be boolean")
    category = str(raw.get("category") or "factual").strip()
    if not category or any(ch.isspace() for ch in category):
        raise ValueError(f"case '{case_id}' has invalid category")
    return EvalCase(
        id=case_id,
        question=question,
        material=str(raw.get("material") or "Все файлы"),
        answer_mode=str(raw.get("answer_mode") or "Обычный"),
        expected_keywords=string_list("expected_keywords"),
        expected_context=string_list("expected_context"),
        expect_refusal=bool(raw.get("expect_refusal", False)),
        forbidden_keywords=string_list("forbidden_keywords"),
        category=category,
        critical=bool(raw.get("critical", False)),
    )


def load_dataset(path: str | Path) -> EvalDataset:
    """Parse a dataset JSON file. ``materials`` paths are resolved relative to
    the dataset file so a dataset is self-contained and relocatable."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("dataset root must be a JSON object")

    base = path.parent
    raw_materials = data.get("materials", [])
    if not isinstance(raw_materials, list) or not all(isinstance(item, str) and item.strip() for item in raw_materials):
        raise ValueError("materials must be a list of non-empty paths")
    materials = [str((base / m).resolve()) for m in raw_materials]

    raw_cases = data.get("cases", [])
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("dataset must define a non-empty 'cases' list")
    cases = [_coerce_case(c, i) for i, c in enumerate(raw_cases)]

    ids = [c.id for c in cases]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate case ids: {sorted(dupes)}")

    return EvalDataset(
        name=str(data.get("name") or path.stem),
        description=str(data.get("description") or ""),
        materials=materials,
        cases=cases,
    )
