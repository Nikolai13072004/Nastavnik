from src.api_models import SummaryRequest, SummaryResponse


def test_summary_request_defaults_to_all_sections():
    request = SummaryRequest(topic="Bluetooth")

    assert request.selected_file == "Все файлы"
    assert request.selected_section == "Все разделы"
    assert request.summary_type == "Средний"
    assert request.topic == "Bluetooth"


def test_summary_response_carries_diagnostics_text():
    response = SummaryResponse(
        text="answer",
        diagnostics="trace",
        trace={"status": "ok"},
    )

    assert response.text == "answer"
    assert response.diagnostics == "trace"
    assert response.trace == {"status": "ok"}
