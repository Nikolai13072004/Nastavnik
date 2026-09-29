import pytest

from scripts.retrieval_profile_benchmark import parse_candidate_counts


def test_parse_candidate_counts_preserves_order_and_removes_duplicates():
    assert parse_candidate_counts("40, 20,40,80", minimum=12) == [40, 20, 80]


def test_parse_candidate_counts_rejects_profile_smaller_than_context_output():
    with pytest.raises(ValueError, match="below RERANK_TOP_K"):
        parse_candidate_counts("8,20", minimum=12)


@pytest.mark.parametrize("raw", ["", " , "])
def test_parse_candidate_counts_requires_a_value(raw):
    with pytest.raises(ValueError, match="at least one"):
        parse_candidate_counts(raw, minimum=12)


def test_parse_candidate_counts_rejects_non_integer():
    with pytest.raises(ValueError, match="invalid candidate count"):
        parse_candidate_counts("20,fast,80", minimum=12)
