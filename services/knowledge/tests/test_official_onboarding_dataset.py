from pathlib import Path

from evals.dataset import load_dataset
from evals.prepare_official_onboarding import build_materials, verify_sources


DATASET = (
    Path(__file__).resolve().parents[1]
    / "evals"
    / "datasets"
    / "official-onboarding.json"
)


def normalize(text: str) -> str:
    return " ".join(text.casefold().replace("ё", "е").split())


def test_official_sources_match_checked_in_extracts() -> None:
    verify_sources()
    for path, expected in build_materials().items():
        assert path.read_text(encoding="utf-8") == expected
        assert path.stat().st_size <= 32_000


def test_questions_have_grounded_evidence_or_expect_refusal() -> None:
    dataset = load_dataset(DATASET)
    materials = {
        Path(path).name: normalize(Path(path).read_text(encoding="utf-8"))
        for path in dataset.materials
    }

    assert len(dataset.materials) == 3
    assert len(dataset.cases) == 30
    assert sum(case.expect_refusal for case in dataset.cases) == 6

    for case in dataset.cases:
        if case.expect_refusal:
            assert not case.expected_context
            assert not case.expected_keywords
            continue

        assert case.material in materials
        assert case.expected_keywords
        assert case.expected_context
        for passage in case.expected_context:
            assert normalize(passage) in materials[case.material], case.id
