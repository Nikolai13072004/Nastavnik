"""Lightweight runtime diagnostics for RAG and summary generation."""
import json
import tempfile
import time
from pathlib import Path
from threading import RLock


_LOCK = RLock()
_LAST_TRACE = None


def _now():
    return time.perf_counter()


def _short_text(text, limit=1200):
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def _chunk_summary(chunk):
    return {
        "source_file": chunk.get("source_file", ""),
        "section": chunk.get("section", ""),
        "chunk_id": chunk.get("chunk_id", 0),
        "score": chunk.get("score"),
        "text_preview": _short_text(chunk.get("text", ""), limit=500),
    }


def start_trace(kind, request):
    """Start a new diagnostic trace."""
    global _LAST_TRACE

    with _LOCK:
        _LAST_TRACE = {
            "kind": kind,
            "request": dict(request or {}),
            "strategy": "",
            "started_at_perf": _now(),
            "elapsed_sec": None,
            "status": "running",
            "events": [],
            "chunks": {},
            "llm_calls": [],
            "prompt_previews": {},
            "output_preview": "",
            "error": "",
        }


def set_strategy(name):
    with _LOCK:
        if _LAST_TRACE is not None:
            _LAST_TRACE["strategy"] = str(name or "")


def add_event(name, **data):
    with _LOCK:
        if _LAST_TRACE is not None:
            _LAST_TRACE["events"].append({
                "name": name,
                "time_offset_sec": round(_now() - _LAST_TRACE["started_at_perf"], 3),
                "data": data,
            })


def record_chunks(label, chunks):
    with _LOCK:
        if _LAST_TRACE is not None:
            _LAST_TRACE["chunks"][label] = [_chunk_summary(chunk) for chunk in (chunks or [])]


def record_chunk_groups(label, groups):
    with _LOCK:
        if _LAST_TRACE is None:
            return

        serialized = []
        for group in groups or []:
            serialized.append({
                "item": group.get("item", ""),
                "chunks": [_chunk_summary(chunk) for chunk in group.get("chunks", [])],
            })

        _LAST_TRACE["chunks"][label] = serialized


def record_prompt(name, prompt):
    with _LOCK:
        if _LAST_TRACE is not None:
            _LAST_TRACE["prompt_previews"][name] = _short_text(prompt, limit=2500)


def record_llm_call(prompt, max_tokens, elapsed_sec, output):
    with _LOCK:
        if _LAST_TRACE is not None:
            _LAST_TRACE["llm_calls"].append({
                "elapsed_sec": round(float(elapsed_sec), 3),
                "prompt_chars": len(str(prompt or "")),
                "max_tokens": max_tokens,
                "output_chars": len(str(output or "")),
                "prompt_preview": _short_text(prompt, limit=1200),
                "output_preview": _short_text(output, limit=800),
            })


def finish_trace(output="", error=None):
    with _LOCK:
        if _LAST_TRACE is not None:
            _LAST_TRACE["elapsed_sec"] = round(_now() - _LAST_TRACE["started_at_perf"], 3)
            _LAST_TRACE["status"] = "error" if error else "ok"
            _LAST_TRACE["output_preview"] = _short_text(output, limit=2500)
            _LAST_TRACE["error"] = str(error or "")


def get_last_trace():
    with _LOCK:
        if _LAST_TRACE is None:
            return None
        return json.loads(json.dumps(_LAST_TRACE, ensure_ascii=False))


def format_last_trace():
    trace = get_last_trace()
    if not trace:
        return "Диагностика пока пуста. Сгенерируйте конспект или ответ."

    lines = [
        f"Статус: {trace.get('status')}",
        f"Тип: {trace.get('kind')}",
        f"Стратегия: {trace.get('strategy') or 'не указана'}",
        f"Время: {trace.get('elapsed_sec')} сек.",
        "",
        "Запрос:",
    ]

    for key, value in trace.get("request", {}).items():
        lines.append(f"- {key}: {value}")

    lines.extend(["", f"LLM-вызовов: {len(trace.get('llm_calls', []))}"])
    for i, call in enumerate(trace.get("llm_calls", []), start=1):
        lines.append(
            f"- #{i}: {call.get('elapsed_sec')} сек., "
            f"prompt={call.get('prompt_chars')} chars, output={call.get('output_chars')} chars"
        )

    lines.append("")
    lines.append("Чанки:")
    for label, value in trace.get("chunks", {}).items():
        if isinstance(value, list) and value and isinstance(value[0], dict) and "chunks" in value[0]:
            lines.append(f"- {label}: {sum(len(group.get('chunks', [])) for group in value)} фрагм.")
            for group in value:
                sources = []
                for chunk in group.get("chunks", []):
                    section = chunk.get("section") or "Без раздела"
                    if section not in sources:
                        sources.append(section)
                lines.append(f"  • {group.get('item')}: {len(group.get('chunks', []))} ({'; '.join(sources[:3]) or 'нет разделов'})")
        else:
            lines.append(f"- {label}: {len(value or [])} фрагм.")
            for chunk in (value or [])[:8]:
                lines.append(
                    f"  • {chunk.get('source_file')} | {chunk.get('section')} | "
                    f"id={chunk.get('chunk_id')} | score={chunk.get('score')}"
                )

    if trace.get("error"):
        lines.extend(["", f"Ошибка: {trace.get('error')}"])

    return "\n".join(lines)


def export_last_trace_json():
    trace = get_last_trace()
    if not trace:
        return None

    path = Path(tempfile.gettempdir()) / f"vedomo_diagnostics_{int(time.time())}.json"
    path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


class DiagnosticLLM:
    """Wraps an LLM engine and records call timings."""

    def __init__(self, wrapped):
        self._wrapped = wrapped

    def call(self, prompt, max_tokens=None, temperature=None):
        started = _now()
        output = self._wrapped.call(prompt, max_tokens=max_tokens, temperature=temperature)
        record_llm_call(
            prompt=prompt,
            max_tokens=max_tokens,
            elapsed_sec=_now() - started,
            output=output,
        )
        return output

    def __getattr__(self, name):
        return getattr(self._wrapped, name)
