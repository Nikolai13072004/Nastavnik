from scripts.large_document_benchmark import build_document, extraction_drill, _dataset


def test_small_large_document_drill_preserves_first_page_fact(tmp_path):
    path = tmp_path / "vedomo-benchmark-5.docx"
    build_document(path, 5)

    result = extraction_drill(path, 5)

    assert result["passed"] is True
    assert result["control_pages"] == [1]
    assert result["missing_control_pages"] == []
    assert result["within_upload_limit"] is True
    assert result["within_index_limit"] is True


def test_dataset_only_includes_control_points_present_in_document(tmp_path):
    path = tmp_path / "vedomo-benchmark-500.docx"

    dataset = _dataset(path)

    assert [case.id for case in dataset.cases] == [
        "first-page-code",
        "quarter-indirect-wait",
        "middle-temperature",
        "unknown-mass",
    ]
