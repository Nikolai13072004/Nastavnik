"""CLI: run a RAG eval dataset and write a report.

    python -m evals.run_eval                         # sample dataset, ingest + score
    python -m evals.run_eval --judge                 # + LLM-as-judge (extra API calls)
    python -m evals.run_eval --dataset evals/datasets/mine.json
    python -m evals.run_eval --workspace-id <id> --no-ingest   # score an existing space

Needs the embedding model and a configured LLM (the same .env the API uses), so
run it on the dev box / server, not in CI.
"""

from __future__ import annotations

# Eval traffic must not throttle or paywall itself - set before config loads.
import os

os.environ.setdefault("QUOTAS_ENABLED", "false")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

from evals.dataset import load_dataset
from evals.runner import report_to_dict, report_to_markdown, run_dataset

_DEFAULT_DATASET = Path(__file__).parent / "datasets" / "sample.json"
_REPORTS_DIR = Path(__file__).parent / "reports"


def _survive_a_legacy_console() -> None:
    """Не дать консоли уронить прогон из-за непечатаемого символа.

    Прогресс и таблица отчёта содержат ✅/❌ — и приходят они из двух разных
    мест (строка результата ``kb.add_book`` и сам отчёт). Консоль Windows по
    умолчанию работает в кодировке системы (cp1251 на русской локали), где этих
    символов нет, поэтому ``print`` падал с ``UnicodeEncodeError`` — прогон
    умирал на строке прогресса, ещё не дойдя ни до одного вопроса.

    Ставим ``errors="replace"`` вместо перехода на UTF-8 намеренно: кириллицу
    cp1251 кодирует прекрасно, и менять кодировку целиком значило бы получить
    кракозябры вместо русского текста. Так «портится» только эмодзи (в `?`), а
    в файлы отчёта они и так пишутся отдельно, с явным ``encoding="utf-8"``.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # поток подменён (перехват в тестах) - не трогаем
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a Vedomo RAG eval dataset.")
    parser.add_argument(
        "--dataset", default=str(_DEFAULT_DATASET), help="path to the dataset JSON"
    )
    parser.add_argument(
        "--threshold", type=float, default=0.6, help="pass threshold per dimension (0..1)"
    )
    parser.add_argument(
        "--judge", action="store_true", help="also score with an LLM judge (extra API calls)"
    )
    parser.add_argument(
        "--retrieval-only", action="store_true",
        help="measure retrieval/context only; skips answer/refusal-only cases and disables HyDE",
    )
    parser.add_argument(
        "--all-files", action="store_true",
        help="search across all dataset materials instead of selecting each case's file",
    )
    parser.add_argument(
        "--stream", action="store_true",
        help="use the production streaming service and record request-start to first token",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help=(
            "сколько раз задать каждый вопрос. Один прогон ловит поломку, но не "
            "измеряет улучшение: замер 04.09.2026 показал, что правка, изменившая "
            "вопрос с 1 верного ответа из 8 на 8 из 8, на наборе с одним прогоном "
            "не видна вовсе. Для сравнения «до и после» берите 4-8."
        ),
    )
    parser.add_argument(
        "--workspace-id", default=None, help="run against an existing indexed workspace"
    )
    parser.add_argument(
        "--no-ingest", action="store_true", help="do not ingest materials (existing workspace)"
    )
    parser.add_argument("--out", default=str(_REPORTS_DIR), help="report output directory")
    args = parser.parse_args(argv)

    if not 0 <= args.threshold <= 1:
        parser.error("--threshold must be between 0 and 1")
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    if args.retrieval_only and args.judge:
        parser.error("--judge cannot be combined with --retrieval-only")
    if args.retrieval_only and args.stream:
        parser.error("--stream cannot be combined with --retrieval-only")

    _survive_a_legacy_console()

    dataset = load_dataset(args.dataset)
    print(f"Dataset '{dataset.name}': {len(dataset.cases)} case(s)")

    report = run_dataset(
        dataset,
        threshold=args.threshold,
        judge=args.judge,
        repeat=args.repeat,
        workspace_id=args.workspace_id,
        ingest=not args.no_ingest,
        retrieval_only=args.retrieval_only,
        stream=args.stream,
        all_files=args.all_files,
    )

    markdown = report_to_markdown(report)
    print("\n" + markdown)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = f"{report.dataset}-{stamp}"
    (out_dir / f"{stem}.md").write_text(markdown, encoding="utf-8")
    (out_dir / f"{stem}.json").write_text(
        json.dumps(report_to_dict(report), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Report written to {out_dir / stem}.{{md,json}}")

    # Non-zero exit when not everything passed, so CI / scripts can gate on it.
    return 0 if report.summary.passed == report.summary.n else 1


if __name__ == "__main__":
    raise SystemExit(main())
