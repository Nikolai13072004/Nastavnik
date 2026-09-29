"""Eval orchestration: ingest -> retrieve -> answer -> score.

This is the only eval module that touches the heavy backend (embedding model +
LLM). It mirrors the production chat path exactly:

    kb.find_section_in_query  ->  kb.search_with_sources  ->  chat_service

so the measured answers are the same ones a user would get, not a parallel
re-implementation. All heavy imports are done inside :func:`run_dataset` so that
``evals.dataset`` / ``evals.scoring`` stay importable in CI without models.
"""

from __future__ import annotations

import os
import json
import statistics
import time
import uuid
from dataclasses import dataclass

from evals.dataset import EvalCase, EvalDataset
from evals.scoring import Aggregate, CaseScore, aggregate_repeated, score_case


@dataclass
class CaseRun:
    """Один вопрос и все его прогоны.

    При ``--repeat 1`` списки состоят из одного элемента, и отчёт выглядит
    ровно как раньше.
    """

    case: EvalCase
    answers: list[str]
    contexts: list[str]
    scores: list[CaseScore]
    retrieval_seconds: list[float] | None = None
    answer_seconds: list[float] | None = None
    total_seconds: list[float] | None = None
    ttft_seconds: list[float | None] | None = None

    @property
    def passes(self) -> int:
        return sum(1 for s in self.scores if s.passed)

    @property
    def repeats(self) -> int:
        return len(self.scores)

    @property
    def unstable(self) -> bool:
        """Прошли не все прогоны и не все провалились - «как повезёт»."""
        return 0 < self.passes < self.repeats

    def _worst(self) -> int:
        """Индекс прогона, который стоит показать в отчёте.

        Показываем ПЕРВЫЙ ПРОВАЛИВШИЙСЯ, а не первый по счёту: разбираться
        всегда нужно с тем, что сломалось, и удачный прогон рядом с ним
        только сбивает. Если провалов нет - показываем первый.
        """
        for i, s in enumerate(self.scores):
            if not s.passed:
                return i
        return 0

    @property
    def score(self) -> CaseScore:
        return self.scores[self._worst()]

    @property
    def answer(self) -> str:
        return self.answers[self._worst()]

    @property
    def context(self) -> str:
        return self.contexts[self._worst()]

    @property
    def context_chars(self) -> int:
        return len(self.context)


@dataclass
class RunReport:
    dataset: str
    workspace_id: str
    runs: list[CaseRun]
    summary: Aggregate
    setup_seconds: float | None = None
    ingestion_seconds: float | None = None
    mode: str = "sync"


def _timing_summary(report: RunReport) -> dict:
    """Deterministic latency summary; first request is the cold observation."""
    retrieval = [v for run in report.runs for v in (run.retrieval_seconds or [])]
    answer = [v for run in report.runs for v in (run.answer_seconds or [])]
    total = [v for run in report.runs for v in (run.total_seconds or [])]
    ttft = [v for run in report.runs for v in (run.ttft_seconds or []) if v is not None]

    def stats(values: list[float]) -> dict | None:
        if not values:
            return None
        ordered = sorted(values)
        # Nearest-rank p95: stable for small pilot samples and never invents an
        # interpolated latency that was not observed.
        p95 = ordered[max(0, (95 * len(ordered) + 99) // 100 - 1)]
        return {"n": len(values), "p50_sec": round(statistics.median(values), 3),
                "p95_sec": round(p95, 3), "max_sec": round(max(values), 3)}

    return {
        "retrieval": stats(retrieval),
        "answer": stats(answer),
        "total": stats(total),
        "cold_first_total_sec": round(total[0], 3) if total else None,
        "warm_total": stats(total[1:]),
        "ttft": stats(ttft),
        "ttft_note": ("time from request start to first non-empty token"
                      if ttft else "not measured or no token events"),
    }


_JUDGE_PROMPT = """Ты - строгий эксперт-экзаменатор. Оцени, насколько ОТВЕТ \
отвечает на ВОПРОС, опираясь на КОНТЕКСТ. Верни только одно число от 1 до 5, \
где 5 - полный и точный ответ, 1 - неверный или пустой. Без пояснений.

ВОПРОС:
{question}

КОНТЕКСТ:
{context}

ОТВЕТ:
{answer}

Оценка (1-5):"""


def _judge_score(llm, *, question: str, context: str, answer: str) -> float | None:
    """Ask the LLM to rate the answer 1..5, normalized to 0..1. Best-effort:
    any parse/transport failure yields ``None`` (dimension simply not scored)."""
    try:
        raw = llm.call(
            _JUDGE_PROMPT.format(question=question, context=context[:4000], answer=answer)
        )
    except Exception:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if not digits:
        return None
    rating = int(digits[0])
    if not 1 <= rating <= 5:
        return None
    return (rating - 1) / 4.0


def run_dataset(
    dataset: EvalDataset,
    *,
    threshold: float = 0.6,
    judge: bool = False,
    repeat: int = 1,
    workspace_id: str | None = None,
    ingest: bool = True,
    retrieval_only: bool = False,
    stream: bool = False,
    all_files: bool = False,
    progress=print,
) -> RunReport:
    """Run every case and return a scored report.

    ``workspace_id`` - run against an existing indexed workspace (skips ingest
    and teardown). When omitted, a throwaway eval workspace is created, the
    dataset materials are ingested into it, and it is cleaned up afterwards.
    """
    # Eval must never be throttled or paywalled by its own traffic.
    os.environ.setdefault("QUOTAS_ENABLED", "false")
    os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

    if repeat < 1:
        raise ValueError("repeat must be positive")
    if retrieval_only and judge:
        raise ValueError("LLM judge is unavailable in retrieval-only mode")
    if retrieval_only and stream:
        raise ValueError("stream cannot be combined with retrieval-only mode")
    from src import app_services, runtime
    from src.api_models import ChatRequest
    import config

    if retrieval_only and config.USE_HYDE:
        raise ValueError("retrieval-only mode requires USE_HYDE=false to avoid provider calls")
    if not ingest and workspace_id is None:
        raise ValueError("--no-ingest requires an existing workspace_id")

    cases = dataset.cases
    if retrieval_only:
        cases = [case for case in cases if case.expected_context]
        skipped = len(dataset.cases) - len(cases)
        progress(f"Retrieval-only: {len(cases)} case(s), {skipped} without expected_context skipped; HyDE disabled")
        if not cases:
            raise ValueError("retrieval-only mode requires cases with expected_context")

    setup_started = time.perf_counter()
    kb = runtime.get_kb(log=lambda *a, **k: None)
    setup_seconds = time.perf_counter() - setup_started
    own_workspace = workspace_id is None
    ws = workspace_id or f"eval-{uuid.uuid4().hex[:12]}"

    ingested: list[str] = []
    ingestion_started = time.perf_counter()
    if own_workspace and ingest:
        if not dataset.materials:
            raise ValueError(
                "dataset has no 'materials' to ingest; pass workspace_id to run "
                "against an already-indexed workspace instead"
            )
        progress(f"Ingesting {len(dataset.materials)} material(s) into {ws} ...")
        try:
            for path in dataset.materials:
                name = os.path.basename(path)
                result = kb.add_book(
                    path, workspace_id=ws, document_id=uuid.uuid4().hex, original_name=name
                )
                ingested.append(name)
                progress(f"  - {name}: {result}")
        except Exception:
            for name in ingested:
                try:
                    kb.remove_book(name, workspace_id=ws)
                except Exception:
                    pass
            raise
    ingestion_seconds = time.perf_counter() - ingestion_started

    runs: list[CaseRun] = []
    llm = runtime.get_llm() if judge else None
    try:
        for case in cases:
            answers: list[str] = []
            contexts: list[str] = []
            scores: list[CaseScore] = []
            retrieval_seconds: list[float] = []
            answer_seconds: list[float] = []
            total_seconds: list[float] = []
            ttft_seconds: list[float | None] = []
            request = ChatRequest(
                message=case.question,
                selected_file="Все файлы" if all_files else case.material,
                answer_mode=case.answer_mode,
            )
            # Контекст берём ИЗ ТОГО ЖЕ конвейера, что и ответ. Раньше здесь
            # шёл отдельный kb.search_with_sources - и он не повторял откат
            # _prepare_chat по _SECTION_FILTER_MIN_SOURCES (сужение по разделу
            # признаётся промахом и поиск идёт заново по всему материалу).
            # Из-за этого recall мерил не тот контекст, из которого получен
            # ответ: замер 04.09.2026 на СП 60 дал recall 0.00 при полностью
            # верном ответе. Метрика, считанная по чужому поиску, не измерение.
            for _ in range(repeat):
                # Поиск делаем ОДИН раз и переиспользуем: он дорогой.
                # Переранжирование 80 кандидатов кросс-энкодером занимает ~35
                # секунд, ответ модели - 2. Раньше здесь шёл отдельный вызов
                # _prepare_chat ради контекста, и каждый вопрос обходился
                # вдвое дороже, не становясь измеренным точнее.
                started = time.perf_counter()
                plan = app_services._prepare_chat(ws, request)
                after_retrieval = time.perf_counter()
                context = plan.context or ""
                if retrieval_only:
                    answer = ""
                    finished = after_retrieval
                    ttft = None
                elif stream:
                    answer = ""
                    first_token = None
                    for line in app_services.chat_stream_service(ws, request, plan=plan):
                        event = json.loads(line)
                        if event.get("type") == "token" and event.get("text"):
                            first_token = first_token or time.perf_counter()
                        elif event.get("type") == "done":
                            answer = event.get("answer") or ""
                        elif event.get("type") == "error":
                            answer = ""
                    finished = time.perf_counter()
                    ttft = (first_token - started) if first_token is not None else None
                else:
                    response = app_services.chat_service(ws, request, plan=plan)
                    finished = time.perf_counter()
                    answer = response.answer or ""
                    ttft = None
                judged = (
                    _judge_score(llm, question=case.question, context=context, answer=answer)
                    if judge
                    else None
                )
                scores.append(
                    score_case(
                        case.id,
                        answer=answer,
                        context=context,
                        expected_keywords=[] if retrieval_only else case.expected_keywords,
                        expected_context=case.expected_context,
                        judge_score=judged,
                        threshold=threshold,
                        expect_refusal=False if retrieval_only else case.expect_refusal,
                        forbidden_keywords=[] if retrieval_only else case.forbidden_keywords,
                    )
                )
                answers.append(answer)
                contexts.append(context)
                retrieval_seconds.append(after_retrieval - started)
                answer_seconds.append(finished - after_retrieval)
                total_seconds.append(finished - started)
                ttft_seconds.append(ttft)

            run = CaseRun(case, answers, contexts, scores,
                          retrieval_seconds, answer_seconds, total_seconds, ttft_seconds)
            if run.repeats == 1:
                mark = "PASS" if run.passes else "FAIL"
            else:
                # Неустойчивый вопрос помечаем отдельно: это не «работает» и
                # не «сломано», а «как повезёт», и читать его надо глазами.
                mark = f"{run.passes}/{run.repeats}"
                if run.unstable:
                    mark += " ~"
            progress(f"  [{mark}] {case.id}")
            runs.append(run)
    finally:
        if own_workspace and ingested:
            for name in ingested:
                try:
                    kb.remove_book(name, workspace_id=ws)
                except Exception:
                    pass

    mode = "retrieval" if retrieval_only else ("stream" if stream else "sync")
    name = f"{dataset.name}-all-files" if all_files else dataset.name
    if mode != "sync":
        name = f"{name}-{mode}"
    return RunReport(name, ws, runs, aggregate_repeated([r.scores for r in runs]),
                     setup_seconds=setup_seconds, ingestion_seconds=ingestion_seconds, mode=mode)


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def report_to_markdown(report: RunReport) -> str:
    s = report.summary
    timing = _timing_summary(report)
    critical = [run for run in report.runs if run.case.critical]
    critical_passed = sum(run.passes == run.repeats for run in critical)
    lines = [
        f"# Eval: {report.dataset}",
        "",
        f"- cases: **{s.n}**, passed: **{s.passed}** (pass rate **{s.pass_rate:.0%}**)",
        f"- critical cases: **{critical_passed}/{len(critical)}**",
        f"- model/KB setup: **{report.setup_seconds:.3f}s**" if report.setup_seconds is not None else "- model/KB setup: **not recorded**",
        f"- material ingestion: **{report.ingestion_seconds:.3f}s**" if report.ingestion_seconds is not None else "- material ingestion: **not recorded**",
    ]
    if s.repeats > 1:
        lines += [
            f"- прогонов на вопрос: **{s.repeats}**; «прошёл» значит прошли ВСЕ",
            f"- устойчивость (доля удачных прогонов): **{s.stability:.0%}**",
            f"- неустойчивых вопросов (прошли не все, но и не все провалились): **{s.unstable}**",
        ]
    if timing["total"]:
        total = timing["total"]
        retrieval = timing["retrieval"]
        answer = timing["answer"]
        lines += [
            f"- latency total p50/p95: **{total['p50_sec']:.3f}s / {total['p95_sec']:.3f}s** (n={total['n']})",
            f"- retrieval p50/p95: **{retrieval['p50_sec']:.3f}s / {retrieval['p95_sec']:.3f}s**",
            f"- answer p50/p95: **{answer['p50_sec']:.3f}s / {answer['p95_sec']:.3f}s**",
            f"- first measured request total: **{timing['cold_first_total_sec']:.3f}s**",
        ]
        if timing["ttft"]:
            # ASCII arrow keeps reports printable in the default cp1251
            # Windows console as well as in UTF-8 CI logs.
            lines.append(f"- TTFT p50/p95: **{timing['ttft']['p50_sec']:.3f}s / {timing['ttft']['p95_sec']:.3f}s** (request start -> first token)")
        else:
            lines.append("- TTFT: **not measured or no token events**")
    lines += [
        f"- mean retrieval recall: **{_fmt(s.mean_recall)}**",
        f"- mean keyword coverage: **{_fmt(s.mean_coverage)}**",
        f"- mean judge score: **{_fmt(s.mean_judge)}**",
        "",
        "| case | category | critical | recall | coverage | judge | pass | missing |",
        "|------|----------|:--------:|:------:|:--------:|:-----:|:----:|---------|",
    ]
    for run in report.runs:
        sc = run.score
        miss = ", ".join(sc.missing_keywords[:4]) or "-"
        if run.repeats > 1:
            verdict = f"{run.passes}/{run.repeats}" + (" ~" if run.unstable else "")
            lines.append(
                f"| {sc.id} | {run.case.category} | {'yes' if run.case.critical else '-'} | {_fmt(sc.retrieval_recall)} | {_fmt(sc.keyword_coverage)} "
                f"| {_fmt(sc.judge_score)} | {verdict} | {miss} |"
            )
            continue
        # ASCII, а не ✅/❌: отчёт печатается в консоль, и на легаси-кодировке
        # (cp1251) оба эмодзи схлопывались в неразличимый "?" - колонка со
        # смыслом "прошёл/не прошёл" переставала что-либо значить. Совпадает со
        # строками прогресса выше, которые и так пишут [PASS]/[FAIL].
        lines.append(
            f"| {sc.id} | {run.case.category} | {'yes' if run.case.critical else '-'} | {_fmt(sc.retrieval_recall)} | {_fmt(sc.keyword_coverage)} "
            f"| {_fmt(sc.judge_score)} | {'PASS' if sc.passed else 'FAIL'} | {miss} |"
        )
    return "\n".join(lines) + "\n"


def report_to_dict(report: RunReport) -> dict:
    s = report.summary
    critical = [run for run in report.runs if run.case.critical]
    critical_passed = sum(run.passes == run.repeats for run in critical)
    return {
        "dataset": report.dataset,
        "workspace_id": report.workspace_id,
        "mode": report.mode,
        "setup_seconds": report.setup_seconds,
        "ingestion_seconds": report.ingestion_seconds,
        "summary": {
            "n": s.n,
            "passed": s.passed,
            "pass_rate": s.pass_rate,
            "mean_recall": s.mean_recall,
            "mean_coverage": s.mean_coverage,
            "mean_judge": s.mean_judge,
            "repeats": s.repeats,
            "stability": s.stability,
            "unstable": s.unstable,
            "critical_n": len(critical),
            "critical_passed": critical_passed,
            "timing": _timing_summary(report),
        },
        "cases": [
            {
                "id": r.score.id,
                "question": r.case.question,
                "category": r.case.category,
                "critical": r.case.critical,
                "answer": r.answer,
                "repeats": r.repeats,
                "passes": r.passes,
                "unstable": r.unstable,
                # Все ответы, а не только показанный в таблице: неустойчивый
                # вопрос разбирается только сравнением прогонов между собой.
                "answers": r.answers,
                "context_chars": r.context_chars,
                # Preview of what retrieval surfaced - to debug recall/answer fails.
                "context_preview": r.context[:1200],
                "retrieval_recall": r.score.retrieval_recall,
                "keyword_coverage": r.score.keyword_coverage,
                "judge_score": r.score.judge_score,
                "passed": r.score.passed,
                "missing_keywords": r.score.missing_keywords,
                "missing_context": r.score.missing_context,
                "retrieval_seconds": r.retrieval_seconds or [],
                "answer_seconds": r.answer_seconds or [],
                "total_seconds": r.total_seconds or [],
                "ttft_seconds": r.ttft_seconds or [],
            }
            for r in report.runs
        ],
    }
