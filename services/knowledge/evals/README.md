# RAG eval harness

Measures answer quality of the Наставник retrieval + answer pipeline so we can
tell whether a prompt / retrieval / model change made things better or worse.
It drives the **real** code path (`_prepare_chat` + sync or streaming chat
service), not a parallel retrieval implementation. A score is evidence for the
tested corpus/configuration, not a universal accuracy claim.

## What it scores

Per question, against an indexed material:

| Dimension | Meaning |
|-----------|---------|
| **retrieval recall** | fraction of `expected_context` substrings present in the retrieved context - *did we fetch the right passages?* |
| **keyword coverage** | fraction of `expected_keywords` present in the answer - *did the answer state the facts?* |
| **judge score** (optional, `--judge`) | LLM-as-judge rating 1..5 (normalized 0..1) of the answer vs the context |

Reports also separate model/KB setup, ingestion, retrieval and answer time and
show p50/p95/max. `--stream` additionally measures request-start → first non-empty
token (TTFT). The first request is shown separately; setup/model load is its own
number. Runs are sequential: these numbers are not a concurrency/load test.

The third-party files listed in `datasets/materials/real-world/manifest.json`
are not redistributed in this repository. Obtain them from their listed sources
and verify their hashes before running the real-world evaluation set. The
synthetic and self-contained sets work without those files.

To compare CPU candidate profiles on the **same existing workspace and the
same questions**, use the dedicated wrapper. It does not change `.env` or the
running server and writes one machine-readable comparison:

```powershell
$env:USE_HYDE="false"
python -m scripts.retrieval_profile_benchmark `
  --workspace-id <id> `
  --dataset evals/datasets/pdd-traps.json `
  --dataset evals/datasets/labour-code-traps.json `
  --candidate-counts 20,40,80 `
  --out evals/reports/retrieval-profiles.json
```

Do not select the fastest row automatically. Critical cases must all pass;
after that compare recall and latency. Keep `RETRIEVAL_TOP_K=80` when there is
no representative pilot dataset.

A case **passes** when every scored dimension clears `--threshold` (default `0.6`).
Empty expectation lists are simply not scored for that case.

## Run it

Needs the embedding model (`BAAI/bge-m3`) and a configured LLM - the same `.env`
the API uses. So run it on the dev box / server, **not** in CI.

```powershell
# Backend deps installed, .env present (LLM_MODE/API_KEY etc.)
python -m evals.run_eval                 # sample dataset: ingest -> ask -> score
python -m evals.run_eval --judge         # also run the LLM judge (extra API calls)
python -m evals.run_eval --threshold 0.7 # stricter pass bar
python -m evals.run_eval --dataset evals/datasets/corporate-product-training.json --stream --repeat 4
```

`--judge` makes additional paid/provider calls. Do not enable it accidentally.
To isolate retrieval without any answer or hidden HyDE provider call:

```powershell
python -m evals.run_eval --dataset evals/datasets/corporate-product-training.json --retrieval-only --repeat 4
```

Retrieval-only requires `USE_HYDE=false`, ignores expected answer keywords, and
skips cases without `expected_context` (normally refusal cases). It cannot prove
answer quality, refusals or TTFT. Its report name ends in `-retrieval` so it is
not confused with the complete run.

Add `--all-files` to search every material in the test workspace instead of
selecting the case's named file. The report name includes `all-files` so the
two search modes cannot be mistaken for one another.

### Corporate product-training set

`datasets/corporate-product-training.json` contains 25 synthetic questions over
three deliberately similar products: exact articles, voltages, installation,
diagnostics, compatibility, comparisons and five out-of-corpus questions. It
marks safety/compatibility cases as `critical`. The bundled manuals are fictional
and safe to share; they validate the harness and demo flow, not client value.

For a real gate, copy the schema to a private location and have the company's
product expert approve the questions, evidence and forbidden claims before the
run. Keep reports private if they contain source previews or model answers.

### Pre-deploy smoke set

`datasets/smoke.json` is the **self-contained** regression gate to run before a
deploy. It ships its own three short materials (biology / history / CS), so it
needs no pre-indexed corpus - it ingests, asks 25 questions, and scores. It
covers factual retrieval, per-file filtering, every answer mode, and - most
importantly - **refusals** (out-of-corpus questions where the bot must decline
instead of hallucinating).

```powershell
python -m evals.run_eval --dataset evals/datasets/smoke.json
```

Eyeball the pass rate (and the refusal cases especially) before shipping a
prompt / retrieval / model change. A drop there means quality regressed.

Reports are written to `evals/reports/<dataset>-<timestamp>.{md,json}` (gitignored).
The process exits non-zero if any case failed, so it can gate a script.

### Score an existing workspace

By default a throwaway eval workspace is created, the dataset `materials` are
ingested into it, and it's cleaned up afterwards. To instead score a workspace
that's already indexed (e.g. your real corpus), use a dataset with empty
`materials` whose questions match that corpus and point the runner at it:

```powershell
python -m evals.run_eval --dataset evals/datasets/network-math.json --workspace-id <id> --no-ingest
```

(Find the id in the DB / admin screen; retrieval filters by `workspace_id` in
the Chroma metadata.)

Two real-corpus datasets ship as examples of this mode (they reference specific
textbooks, so they only mean something against a workspace where those files are
indexed):

- `datasets/network-math.json` - Tanenbaum *Computer Networks* + Krasnov
  *Operational Calculus* (term retrieval + answer keyword checks).
- `datasets/literature.json` - Andersen's *Little Match Girl* + Plato's
  *Symposium* (file / section retrieval recall).

### Наборы-ловушки

Три набора меряют не «умеет ли отвечать», а **врёт ли уверенно** - то есть
единственный отказ, из-за которого продуктом перестают пользоваться: человек
поверил ответу, поступил по нему и получил выговор.

- `datasets/labour-code-traps.json` - ТК РФ (рабочее время, отпуска, оплата):
  потерянные оговорки и исключения.
- `datasets/pdd-traps.json` - ПДД РФ: ложная предпосылка и усиление правила
  («полностью запрещают»).
- `datasets/drug-leaflets-traps.json` - инструкции к трём почти одинаковым
  препаратам витаминов B: кросс-документная путаница (один состав, разные
  формы, дозы и возрастные ограничения) плюс оба предыдущих типа.

Документ в последнем наборе намеренно маленький (11 тыс. знаков): на таком
объёме поиск находит всё, поэтому набор меряет рассуждение, а не retrieval -
recall на нём равен 1.00 во всех кейсах, и любой провал принадлежит модели.

Все три запускаются против уже проиндексированного пространства
(`--workspace-id <id> --no-ingest`).

**Читайте ответы, а не только таблицу.** За две сессии работы с этими наборами
метрика соврала семь раз - и каждый раз ошибка была в наборе, а не в продукте:
слишком буквальное ожидаемое слово, «ё» против «е», неучтённая формулировка
отказа, запрет на слова самого вопроса. Цифра, за которой не прочитаны ответы,
измерением не является.

## Dataset format

`evals/datasets/sample.json`:

```json
{
  "name": "sample-bio",
  "description": "...",
  "materials": ["materials/bio-photosynthesis.txt"],
  "cases": [
    {
      "id": "photosynthesis-products",
      "question": "Что такое фотосинтез и какие у него конечные продукты?",
      "material": "Все файлы",
      "answer_mode": "Обычный",
      "expected_keywords": ["свет", "углекислый газ", "глюкоз", "кислород"],
      "expected_context": ["фотосинтез", "хлоропласт"]
    }
  ]
}
```

- `materials` - paths relative to the dataset file; ingested into the eval space.
- `material` - per-case file filter (`"Все файлы"` = search everything); use it to
  test that the right material is selected when several are indexed.
- `answer_mode` - one of `Обычный` / `Кратко` / `Подробно` / `Только цитаты`.
- `expected_keywords` / `expected_context` - case-insensitive substrings. Pick
  short word stems (`глюкоз`, `митохондри`) so inflections still match.
- `category` - stable no-space label used to group failures (`safety`,
  `compatibility`, `comparison`, and so on).
- `critical` - boolean. Reports show critical cases separately; a critical
  failure must be reviewed even when aggregate percentages look acceptable.
- `expect_refusal` / `forbidden_keywords` - honest out-of-corpus behavior and
  statements the answer must not assert. Automated substring scoring remains a
  screening tool; an expert must read the critical answers.

## Layout

| File | Role |
|------|------|
| `dataset.py` | schema + loader (pure stdlib) |
| `scoring.py` | scoring functions (pure - unit-tested in CI) |
| `runner.py`  | ingest -> retrieve -> answer -> score (lazy heavy imports) |
| `run_eval.py`| CLI entrypoint |
| `datasets/`  | datasets + their material files |

CI exercises schema/scoring/reporting and mocked sync/stream orchestration without
loading models or calling an LLM. A real model run remains a controlled local or
pilot-environment step.
