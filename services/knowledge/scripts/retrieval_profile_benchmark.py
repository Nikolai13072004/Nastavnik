"""Compare retrieval candidate profiles on one existing workspace.

The command deliberately runs the production ``_prepare_chat`` path through
the existing eval harness. It changes only ``config.RETRIEVAL_TOP_K`` inside
this short-lived process, so running it cannot silently change the server.

Example:
    python -m scripts.retrieval_profile_benchmark \
      --workspace-id <id> \
      --dataset evals/datasets/pdd-traps.json \
      --dataset evals/datasets/labour-code-traps.json \
      --candidate-counts 20,40,80 \
      --out evals/reports/retrieval-profiles.json
"""

from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path


def parse_candidate_counts(raw: str, *, minimum: int) -> list[int]:
    """Parse a stable, de-duplicated list and reject unsafe profiles."""
    counts: list[int] = []
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        try:
            count = int(value)
        except ValueError as exc:
            raise ValueError(f"invalid candidate count: {value!r}") from exc
        if count < minimum:
            raise ValueError(
                f"candidate count {count} is below RERANK_TOP_K={minimum}"
            )
        if count not in counts:
            counts.append(count)
    if not counts:
        raise ValueError("at least one candidate count is required")
    return counts


def _summary(report_dict: dict, *, candidate_count: int) -> dict:
    summary = report_dict["summary"]
    return {
        "dataset": report_dict["dataset"],
        "candidate_count": candidate_count,
        "cases": summary["n"],
        "passed": summary["passed"],
        "pass_rate": summary["pass_rate"],
        "critical_cases": summary["critical_n"],
        "critical_passed": summary["critical_passed"],
        "stability": summary["stability"],
        "timing": summary["timing"],
        "failed_case_ids": [
            case["id"] for case in report_dict["cases"] if not case["passed"]
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare 20/40/80 retrieval profiles on identical questions."
    )
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument(
        "--dataset",
        action="append",
        required=True,
        help="dataset JSON; pass the option more than once to compare several sets",
    )
    parser.add_argument("--candidate-counts", default="20,40,80")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--out", help="optional JSON result path")
    args = parser.parse_args(argv)

    if args.repeat < 1:
        parser.error("--repeat must be positive")

    # Heavy imports stay inside main so parser unit tests never load the models.
    import config
    from evals.dataset import load_dataset
    from evals.runner import report_to_dict, run_dataset

    if config.USE_HYDE:
        parser.error("set USE_HYDE=false: retrieval comparison must not call an LLM")
    try:
        counts = parse_candidate_counts(
            args.candidate_counts, minimum=config.RERANK_TOP_K
        )
    except ValueError as exc:
        parser.error(str(exc))

    datasets = [load_dataset(path) for path in args.dataset]
    results: list[dict] = []
    for count in counts:
        config.RETRIEVAL_TOP_K = count
        for dataset in datasets:
            print(f"Profile {count}: {dataset.name}")
            report = run_dataset(
                dataset,
                workspace_id=args.workspace_id,
                ingest=False,
                retrieval_only=True,
                repeat=args.repeat,
            )
            results.append(
                _summary(report_to_dict(report), candidate_count=count)
            )

    payload = {
        "workspace_id": args.workspace_id,
        "repeat": args.repeat,
        "environment": {
            "python": platform.python_version(),
            "embedding_model": config.EMBEDDING_MODEL,
            "embedding_device": config.EMBEDDING_DEVICE,
            "reranker_enabled": config.USE_RERANKER,
            "reranker_model": config.RERANKER_MODEL,
            "reranker_device": config.RERANKER_DEVICE,
            "rerank_top_k": config.RERANK_TOP_K,
        },
        "profiles": results,
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    print(rendered)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")

    # A comparison is evidence even when one profile fails. Operators decide
    # from the JSON which profile is acceptable; argument/runtime errors still
    # return non-zero through argparse or exceptions.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
