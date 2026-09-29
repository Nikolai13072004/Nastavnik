from src.diagnostics import (
    DiagnosticLLM,
    finish_trace,
    format_last_trace,
    get_last_trace,
    record_chunks,
    start_trace,
)


class FakeLLM:
    def call(self, prompt, max_tokens=None, temperature=None):
        return f"ok:{max_tokens}:{temperature}:{prompt}"


def test_diagnostic_llm_records_call():
    start_trace("summary", {"topic": "Bluetooth"})

    llm = DiagnosticLLM(FakeLLM())
    output = llm.call("hello", max_tokens=128, temperature=0.2)
    finish_trace(output=output)

    trace = get_last_trace()
    assert trace["status"] == "ok"
    assert trace["output_preview"].startswith("ok:128:0.2")
    assert len(trace["llm_calls"]) == 1
    assert trace["llm_calls"][0]["prompt_chars"] == len("hello")
    assert trace["llm_calls"][0]["max_tokens"] == 128


def test_format_last_trace_lists_recorded_chunks():
    start_trace("summary", {"topic": "WiMAX"})
    record_chunks(
        "direct_topic",
        [
            {
                "source_file": "networks.pdf",
                "section": "Глава 4",
                "chunk_id": 42,
                "score": 0.91,
                "text": "802.16 WiMAX",
            }
        ],
    )
    finish_trace(output="done")

    formatted = format_last_trace()
    assert "Статус: ok" in formatted
    assert "direct_topic: 1" in formatted
    assert "networks.pdf" in formatted
    assert "Глава 4" in formatted
