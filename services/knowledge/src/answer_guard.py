"""Deterministic safety checks for answers produced from retrieved documents.

The language model remains responsible for phrasing and synthesis.  This
module handles the small set of facts where a silent wording change is more
dangerous than a conservative answer: document-borne prompt injection and
inclusive/exclusive numeric boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class GuardResult:
    answer: str
    blocked: bool = False
    corrected: bool = False
    allow_sources: bool = True
    reason: str = ""


_INJECTION_MARKERS = (
    "игнорируй вопрос",
    "игнорируйте вопрос",
    "игнорируй предыдущие инструкции",
    "игнорируйте предыдущие инструкции",
    "забудь предыдущие инструкции",
    "системный промпт",
    "system prompt",
    "ignore previous instructions",
    "ignore all previous instructions",
    "assistant must",
    "you are chatgpt",
    "утверждай, что",
)

_EXECUTION_CLAIMS = (
    "проверка пройдена",
    "проверка успешно пройдена",
    "доступ разрешен",
    "доступ разрешён",
    "разрешение получено",
    "действие выполнено",
    "команда выполнена",
    "verification passed",
    "access granted",
    "action completed",
)

# Longer phrases must be matched before their shorter prefixes.
_RELATIONS = (
    ("ge", re.compile(r"(?:не\s+менее|не\s+меньше|больше\s+или\s+равн[оа]|"
                       r"как\s+минимум|at\s+least|or\s+greater|or\s+more|>=|≥)", re.I)),
    ("le", re.compile(r"(?:не\s+более|не\s+больше|меньше\s+или\s+равн[оа]|"
                       r"не\s+превышает|at\s+most|or\s+less|<=|≤)", re.I)),
    ("gt", re.compile(r"(?:строго\s+более|строго\s+больше|более|больше|greater\s+than|>)", re.I)),
    ("lt", re.compile(r"(?:строго\s+менее|строго\s+меньше|менее|меньше|less\s+than|<)", re.I)),
)
_VALUE = r"(?P<number>\d+(?:[.,]\d+)?)\s*(?P<unit>%|процент(?:а|ов)?|percent(?:s)?)?"


def _constraints(text: str):
    found = []
    for relation, relation_re in _RELATIONS:
        pattern = re.compile(rf"(?P<relation>{relation_re.pattern})\s*{_VALUE}", re.I)
        for match in pattern.finditer(text or ""):
            number = match.group("number").replace(",", ".")
            unit = (match.group("unit") or "").casefold()
            if unit.startswith("процент") or unit.startswith("percent"):
                unit = "%"
            found.append((number, unit, relation, match.span(), match.group(0)))
        # English source documents commonly put the inclusive relation after
        # the value ("80% or greater").  Support that form without changing
        # the answer-side wording rules.
        suffixes = {
            "ge": r"(?:or\s+greater|or\s+more|или\s+больше|или\s+более)",
            "le": r"(?:or\s+less|or\s+fewer|или\s+меньше|или\s+менее)",
        }
        if relation in suffixes:
            suffix_pattern = re.compile(
                rf"{_VALUE}\s*(?P<relation>{suffixes[relation]})", re.I
            )
            for match in suffix_pattern.finditer(text or ""):
                number = match.group("number").replace(",", ".")
                unit = (match.group("unit") or "").casefold()
                if unit.startswith("процент") or unit.startswith("percent"):
                    unit = "%"
                found.append((number, unit, relation, match.span(), match.group(0)))
    return found


def _canonical_relation(relation: str) -> str:
    return {"ge": "не менее", "le": "не более", "gt": "более", "lt": "менее"}[relation]


def _repair_numeric_boundaries(context: str, answer: str) -> tuple[str, bool]:
    context_constraints = _constraints(context)
    answer_constraints = _constraints(answer)
    if not context_constraints or not answer_constraints:
        return answer, False

    # Only correct an unambiguous source boundary.  If two retrieved fragments
    # genuinely disagree, preserving that conflict is safer than choosing one.
    by_value: dict[tuple[str, str], set[str]] = {}
    for number, unit, relation, _span, _raw in context_constraints:
        by_value.setdefault((number, unit), set()).add(relation)

    replacements = []
    for number, unit, answer_relation, span, _raw in answer_constraints:
        source_relations = by_value.get((number, unit))
        if not source_relations or len(source_relations) != 1:
            continue
        source_relation = next(iter(source_relations))
        if source_relation == answer_relation:
            continue
        rendered_number = number.replace(".", ",")
        rendered_unit = unit
        replacements.append((span[0], span[1], f"{_canonical_relation(source_relation)} {rendered_number}{rendered_unit}"))

    if not replacements:
        return answer, False
    result = answer
    for start, end, replacement in reversed(replacements):
        result = result[:start] + replacement + result[end:]
    return result, True


def guard_answer(*, question: str, context: str, answer: str) -> GuardResult:
    """Validate one generated answer without making another model call."""
    text = str(answer or "").strip()
    context_lower = (context or "").casefold()
    answer_lower = text.casefold()

    has_injection = any(marker in context_lower for marker in _INJECTION_MARKERS)
    claims_execution = any(claim in answer_lower for claim in _EXECUTION_CLAIMS)
    if has_injection and claims_execution:
        return GuardResult(
            answer=(
                "В материале обнаружена инструкция, адресованная ассистенту. "
                "Она рассматривается как содержимое документа и не выполняется. "
                "По этому фрагменту нельзя утверждать, что проверка пройдена, "
                "доступ разрешён или действие выполнено."
            ),
            blocked=True,
            allow_sources=False,
            reason="document_prompt_injection",
        )

    repaired, corrected = _repair_numeric_boundaries(context, text)
    reason = "numeric_boundary_corrected" if corrected else ""
    if "[ocr: числовой фрагмент" in context_lower and re.search(r"\d", repaired):
        warning = "Точное числовое значение в этом месте нужно сверить с оригиналом: OCR распознал его неуверенно."
        if warning.casefold() not in repaired.casefold():
            repaired = f"{repaired.rstrip()}\n\n{warning}"
            corrected = True
            reason = "ocr_numeric_uncertainty"

    return GuardResult(answer=repaired, corrected=corrected, reason=reason)
