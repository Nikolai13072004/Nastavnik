"""Stage 41: request models reject oversized payloads (length / item caps)."""

import pytest
from pydantic import ValidationError

from src.api_models import (
    ChatRequest,
    CreateAssignmentRequest,
    QuizQuestion,
    SubmitAttemptRequest,
    SummaryExportRequest,
)


def test_chat_message_length_capped():
    with pytest.raises(ValidationError):
        ChatRequest(message="x" * 50_001)


def test_chat_history_item_cap():
    msgs = [{"role": "user", "content": "hi"}] * 201
    with pytest.raises(ValidationError):
        ChatRequest(history=msgs)


def test_summary_export_text_capped():
    with pytest.raises(ValidationError):
        SummaryExportRequest(text="x" * 500_001)


def test_assignment_questions_cap():
    q = {"question": "q", "options": ["a", "b"], "correct_index": 0}
    with pytest.raises(ValidationError):
        CreateAssignmentRequest(questions=[q] * 201)


def test_submit_answers_cap():
    with pytest.raises(ValidationError):
        SubmitAttemptRequest(answers=[0] * 501)


def test_reasonable_values_ok():
    ChatRequest(message="обычный вопрос")
    SummaryExportRequest(text="небольшой конспект")
    CreateAssignmentRequest(
        questions=[QuizQuestion(question="q", options=["a", "b"], correct_index=0)]
    )
