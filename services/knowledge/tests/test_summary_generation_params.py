from src.summary_engine import _summary_generation_params


def test_detailed_summary_uses_more_context_and_tokens_than_medium():
    medium = _summary_generation_params("Средний")
    detailed = _summary_generation_params("Подробный")

    assert detailed["top_k"] > medium["top_k"]
    assert detailed["chunk_tokens"] > medium["chunk_tokens"]
    assert detailed["final_tokens"] > medium["final_tokens"]


def test_short_summary_uses_less_context_than_medium():
    short = _summary_generation_params("Краткий")
    medium = _summary_generation_params("Средний")

    assert short["top_k"] < medium["top_k"]
    assert short["final_tokens"] < medium["final_tokens"]
