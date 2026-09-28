"""CI-safe tests for the RAG eval harness.

Exercises only the pure pieces - dataset parsing + scoring + report formatting -
so no embedding model or LLM is loaded. The full pipeline run (``evals.runner``)
is a manual/local step, intentionally not in CI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.dataset import EvalCase, EvalDataset, load_dataset
from evals.runner import CaseRun, RunReport, report_to_dict, report_to_markdown, run_dataset
from evals.scoring import (
    aggregate,
    aggregate_repeated,
    coverage,
    looks_like_refusal,
    missing,
    score_case,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DATASETS_DIR = _REPO_ROOT / "evals" / "datasets"
_SAMPLE = _DATASETS_DIR / "sample.json"


# --- scoring ---------------------------------------------------------------

def test_coverage_is_case_insensitive_fraction():
    assert coverage("Свет и Глюкоза", ["свет", "глюкоз"]) == 1.0
    assert coverage("только свет", ["свет", "кислород"]) == 0.5
    assert coverage("anything", []) is None  # nothing to score


def test_missing_lists_absent_needles():
    assert missing("есть свет", ["свет", "вода"]) == ["вода"]
    assert missing("36-38 АТФ", ["36", "АТФ"]) == []


def test_score_case_passes_when_all_scored_dims_clear_threshold():
    score = score_case(
        "c1",
        answer="свет, углекислый газ, глюкоза и кислород",
        context="фотосинтез в хлоропласте",
        expected_keywords=["свет", "глюкоз", "кислород"],
        expected_context=["фотосинтез", "хлоропласт"],
        threshold=0.6,
    )
    assert score.retrieval_recall == 1.0
    assert score.keyword_coverage == 1.0
    assert score.passed is True
    assert score.missing_keywords == []


def test_score_case_fails_on_low_coverage():
    score = score_case(
        "c2",
        answer="не знаю",
        context="фотосинтез",
        expected_keywords=["свет", "глюкоз", "кислород"],
        expected_context=["фотосинтез"],
        threshold=0.6,
    )
    assert score.keyword_coverage == 0.0
    assert score.passed is False
    assert set(score.missing_keywords) == {"свет", "глюкоз", "кислород"}


def test_judge_dimension_can_block_pass():
    score = score_case(
        "c3",
        answer="свет глюкоза кислород",
        context="фотосинтез",
        expected_keywords=["свет"],
        expected_context=["фотосинтез"],
        judge_score=0.25,
        threshold=0.6,
    )
    assert score.passed is False  # judge below threshold


def test_case_with_no_expectations_is_a_fail():
    score = score_case("empty", answer="x", context="y", expected_keywords=[], expected_context=[])
    assert score.retrieval_recall is None
    assert score.keyword_coverage is None
    assert score.passed is False


def test_looks_like_refusal_detects_canned_no_info():
    assert looks_like_refusal("Информация по данному вопросу не найдена в загруженных материалах.")
    assert looks_like_refusal("НЕТ ИНФОРМАЦИИ - база пуста")
    assert not looks_like_refusal("Фотосинтез - это процесс преобразования света.")


def test_expect_refusal_passes_when_bot_declines():
    score = score_case(
        "neg",
        answer="Информация по данному вопросу не найдена в загруженных материалах.",
        context="",
        expected_keywords=[],
        expected_context=[],
        expect_refusal=True,
    )
    assert score.passed is True
    # Keyword/recall aren't scored for a refusal case.
    assert score.retrieval_recall is None
    assert score.keyword_coverage is None


def test_expect_refusal_fails_when_bot_answers_anyway():
    score = score_case(
        "neg",
        answer="Фотосинтез - это преобразование света в энергию.",
        context="",
        expected_keywords=[],
        expected_context=[],
        expect_refusal=True,
    )
    assert score.passed is False
    assert score.missing_keywords  # carries the "ожидался отказ" marker


def test_aggregate_means_skip_unscored_dimensions():
    scores = [
        score_case("a", answer="свет", context="фотосинтез",
                   expected_keywords=["свет"], expected_context=["фотосинтез"]),
        score_case("b", answer="нет", context="пусто",
                   expected_keywords=["глюкоз"], expected_context=[]),
    ]
    agg = aggregate(scores)
    assert agg.n == 2
    assert agg.passed == 1
    assert agg.pass_rate == 0.5
    # only case "a" contributed a recall value
    assert agg.mean_recall == 1.0
    assert agg.mean_coverage == 0.5  # (1.0 + 0.0) / 2


# --- dataset loading -------------------------------------------------------

def test_sample_dataset_loads_and_resolves_material_paths():
    ds = load_dataset(_SAMPLE)
    assert ds.name == "sample-bio"
    assert len(ds.cases) == 5
    assert {c.id for c in ds.cases} >= {"photosynthesis-products", "atp-yield"}
    assert len(ds.materials) == 1
    material = Path(ds.materials[0])
    assert material.is_absolute()
    assert material.exists()
    assert material.name == "bio-photosynthesis.txt"


@pytest.mark.parametrize(
    "path", sorted(_DATASETS_DIR.glob("*.json")), ids=lambda p: p.name
)
def test_every_shipped_dataset_loads(path):
    """Every dataset that ships in the repo must parse (unique ids, valid keys)
    and, if it's self-contained, its material files must exist on disk."""
    ds = load_dataset(path)
    assert ds.cases
    assert len({c.id for c in ds.cases}) == len(ds.cases)
    datasets_dir = _DATASETS_DIR.resolve()
    for material in ds.materials:
        # Self-contained datasets bundle their materials under evals/ (these
        # must exist). Real-corpus datasets reference local-only files (e.g.
        # ../../docs/*) that are intentionally not committed - don't require
        # those in CI, where there is no docs/.
        if datasets_dir in Path(material).resolve().parents:
            assert Path(material).exists(), f"{path.name}: missing bundled material {material}"


def test_smoke_dataset_is_self_contained_with_refusals_and_all_modes():
    ds = load_dataset(_DATASETS_DIR / "smoke.json")
    assert len(ds.cases) >= 20
    assert len(ds.materials) == 3
    assert all(Path(m).exists() for m in ds.materials)
    # Negative cases are the anti-hallucination guard - keep several.
    assert sum(1 for c in ds.cases if c.expect_refusal) >= 3
    # Smoke set must exercise every answer mode the UI offers.
    assert {c.answer_mode for c in ds.cases} >= {
        "Обычный",
        "Кратко",
        "Подробно",
        "Только цитаты",
    }


def test_corporate_dataset_covers_product_training_risks():
    ds = load_dataset(_DATASETS_DIR / "corporate-product-training.json")
    assert len(ds.cases) == 25
    assert len(ds.materials) == 3 and all(Path(path).exists() for path in ds.materials)
    assert sum(case.expect_refusal for case in ds.cases) == 5
    assert sum(case.critical for case in ds.cases) >= 10
    assert {case.category for case in ds.cases} >= {
        "exact_id", "constraint", "installation", "safety", "diagnostics",
        "compatibility", "comparison", "procedure", "refusal",
    }
    material_text = "\n".join(Path(path).read_text(encoding="utf-8") for path in ds.materials).casefold()
    for case in ds.cases:
        if not case.expect_refusal:
            assert case.expected_context, f"{case.id}: answerable case needs retrieval evidence"
        for phrase in case.expected_context:
            assert phrase.casefold() in material_text, f"{case.id}: missing evidence {phrase!r}"


def test_loader_rejects_missing_question(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"cases": [{"id": "x"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="question"):
        load_dataset(bad)


def test_loader_rejects_duplicate_ids(tmp_path):
    bad = tmp_path / "dupe.json"
    bad.write_text(
        json.dumps({"cases": [
            {"id": "x", "question": "a?"},
            {"id": "x", "question": "b?"},
        ]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_dataset(bad)


def test_loader_rejects_unknown_keys(tmp_path):
    bad = tmp_path / "unknown.json"
    bad.write_text(
        json.dumps({"cases": [{"id": "x", "question": "a?", "typo_field": 1}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown keys"):
        load_dataset(bad)


def test_loader_rejects_empty_cases(tmp_path):
    bad = tmp_path / "empty.json"
    bad.write_text(json.dumps({"cases": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="non-empty"):
        load_dataset(bad)


@pytest.mark.parametrize("field,value", [
    ("expected_keywords", "not-a-list"),
    ("expected_context", [""]),
    ("forbidden_keywords", [1]),
    ("critical", "false"),
    ("expect_refusal", 1),
    ("category", "two words"),
])
def test_loader_rejects_ambiguous_case_types(tmp_path, field, value):
    bad = tmp_path / "bad-types.json"
    bad.write_text(json.dumps({"cases": [{"id": "x", "question": "?", field: value}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_dataset(bad)


# --- report formatting -----------------------------------------------------

def _report() -> RunReport:
    case = EvalCase(id="c1", question="Что такое фотосинтез?")
    score = score_case(
        "c1",
        answer="свет и глюкоза",
        context="фотосинтез",
        expected_keywords=["свет"],
        expected_context=["фотосинтез"],
    )
    runs = [
        CaseRun(
            case=case,
            answers=["свет и глюкоза"],
            contexts=["фотосинтез"],
            scores=[score],
        )
    ]
    return RunReport(
        dataset="t", workspace_id="eval-x", runs=runs, summary=aggregate_repeated([[score]])
    )


def test_report_markdown_has_summary_and_rows():
    md = report_to_markdown(_report())
    assert "pass rate" in md
    assert "c1" in md
    assert "100%" in md


def test_report_dict_is_json_serializable():
    payload = report_to_dict(_report())
    json.dumps(payload, ensure_ascii=False)  # must not raise
    assert payload["summary"]["n"] == 1
    assert payload["cases"][0]["id"] == "c1"


def test_report_records_latency_without_claiming_ttft():
    report = _report()
    report.runs[0].retrieval_seconds = [1.0, 2.0, 3.0]
    report.runs[0].answer_seconds = [4.0, 5.0, 6.0]
    report.runs[0].total_seconds = [5.0, 7.0, 9.0]
    report.runs[0].ttft_seconds = [2.0, 3.0, 4.0]
    report.setup_seconds = 2.5
    report.ingestion_seconds = 1.25
    payload = report_to_dict(report)
    timing = payload["summary"]["timing"]
    assert timing["retrieval"] == {"n": 3, "p50_sec": 2.0, "p95_sec": 3.0, "max_sec": 3.0}
    assert timing["total"]["p50_sec"] == 7.0
    assert timing["warm_total"]["p50_sec"] == 8.0
    assert timing["ttft"]["p50_sec"] == 3.0
    markdown = report_to_markdown(report)
    assert "retrieval p50/p95" in markdown and "TTFT p50/p95" in markdown


def test_non_streaming_report_does_not_invent_ttft():
    payload = report_to_dict(_report())
    assert payload["summary"]["timing"]["ttft"] is None
    assert "TTFT: **not measured" not in report_to_markdown(_report())  # no latency samples at all


def test_report_marks_critical_cases_separately():
    report = _report()
    report.runs[0].case.critical = True
    payload = report_to_dict(report)
    assert payload["summary"]["critical_n"] == 1
    assert payload["summary"]["critical_passed"] == 1
    assert payload["cases"][0]["critical"] is True


def test_stream_eval_reuses_plan_and_records_ttft(monkeypatch):
    from types import SimpleNamespace
    import evals.runner as runner
    from src import app_services, runtime

    plan = SimpleNamespace(context="expected context")
    monkeypatch.setattr(runtime, "get_kb", lambda log=None: object())
    monkeypatch.setattr(app_services, "_prepare_chat", lambda workspace, request: plan)
    clock = iter([0.0, 2.0, 3.0, 4.25, 10.0, 11.5, 13.0, 18.0])
    monkeypatch.setattr(runner.time, "perf_counter", lambda: next(clock))

    def stream(workspace, request, *, plan=None):
        assert plan is not None and plan.context == "expected context"
        yield json.dumps({"type": "token", "text": "answer"}) + "\n"
        yield json.dumps({"type": "done", "answer": "answer"}) + "\n"

    monkeypatch.setattr(app_services, "chat_stream_service", stream)
    dataset = EvalDataset("timing", "", [], [EvalCase(
        id="one", question="?", expected_keywords=["answer"],
        expected_context=["expected"], critical=True,
    )])
    report = run_dataset(dataset, workspace_id="existing", ingest=False, stream=True, progress=lambda _: None)
    assert report.mode == "stream" and report.dataset == "timing-stream"
    assert report.summary.passed == 1
    assert report.setup_seconds == 2.0 and report.ingestion_seconds == 1.25
    assert report.runs[0].retrieval_seconds == [1.5]
    assert report.runs[0].answer_seconds == [6.5]
    assert report.runs[0].total_seconds == [8.0]
    assert report.runs[0].ttft_seconds == [3.0]


def test_retrieval_only_skips_refusal_and_never_calls_answer(monkeypatch):
    from types import SimpleNamespace
    import config
    from src import app_services, runtime

    monkeypatch.setattr(config, "USE_HYDE", False)
    monkeypatch.setattr(runtime, "get_kb", lambda log=None: object())
    monkeypatch.setattr(app_services, "_prepare_chat",
                        lambda workspace, request: SimpleNamespace(context="evidence"))
    monkeypatch.setattr(app_services, "chat_service",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM called")))
    dataset = EvalDataset("retrieval", "", [], [
        EvalCase(id="answerable", question="?", expected_context=["evidence"]),
        EvalCase(id="refusal", question="?", expect_refusal=True),
    ])
    progress = []
    report = run_dataset(dataset, workspace_id="existing", ingest=False,
                         retrieval_only=True, repeat=2, progress=progress.append)
    assert report.mode == "retrieval" and report.summary.n == 1
    assert report.summary.passed == 1 and report.summary.repeats == 2
    assert any("1 without expected_context skipped" in line for line in progress)


@pytest.mark.parametrize("kwargs,match", [
    ({"repeat": 0}, "repeat"),
    ({"ingest": False}, "workspace_id"),
    ({"workspace_id": "x", "ingest": False, "stream": True, "retrieval_only": True}, "combined"),
])
def test_runner_rejects_ambiguous_modes(monkeypatch, kwargs, match):
    from src import runtime
    monkeypatch.setattr(runtime, "get_kb", lambda log=None: object())
    dataset = EvalDataset("x", "", [], [EvalCase(id="x", question="?", expected_context=["x"])])
    with pytest.raises(ValueError, match=match):
        run_dataset(dataset, progress=lambda _: None, **kwargs)


def test_coverage_treats_yo_and_ye_as_the_same_letter():
    """«ё»/«е» в русском взаимозаменяемы, и модель выбирает букву свободно.

    Реальный случай (02.09.2026): ответ «удлиненный основной отпуск» не совпал
    с ожиданием «удлинённ» и пошёл в провалы, хотя исключение из ТК был назван
    верно. Такое расхождение говорит о букве в датасете, а не о качестве RAG.
    """
    from evals.scoring import coverage, missing

    assert coverage("удлиненный основной отпуск", ["удлинённ"]) == 1.0
    assert coverage("предоставляется удлинённый отпуск", ["удлиненн"]) == 1.0
    assert missing("удлиненный отпуск", ["удлинённ"]) == []


# --- Запрещённые слова -----------------------------------------------------
# Проверка «упомянуты ли нужные слова» слепа к ответу, который начинается с
# неправды, а нужные слова приводит следом. Замер 04.09.2026: ответ «ПДД
# полностью запрещают обгон... согласно п.11.4, на нерегулируемых при движении
# по дороге, не являющейся главной» получил PASS с покрытием 1.00, хотя первая
# фраза прямо ложна и снабжена выдуманным обоснованием.


def test_forbidden_keyword_fails_the_case_despite_full_coverage():
    from evals.scoring import score_case

    answer = (
        "ПДД полностью запрещают обгон на перекрёстках. Согласно пункту 11.4, "
        "обгон запрещён на нерегулируемых перекрестках при движении по дороге, "
        "не являющейся главной."
    )
    sc = score_case(
        "premise",
        answer=answer,
        context="обгон запрещен ... не являющейся главной",
        expected_keywords=["главн", "нерегулируем"],
        expected_context=["не являющейся главной"],
        forbidden_keywords=["полностью запрещ"],
    )
    assert sc.keyword_coverage == 1.0, "нужные слова в ответе действительно есть"
    assert not sc.passed, "но кейс обязан провалиться из-за запрещённой формулировки"
    assert any("ЗАПРЕЩЕНО" in m for m in sc.missing_keywords)


def test_case_passes_when_the_forbidden_phrasing_is_absent():
    from evals.scoring import score_case

    sc = score_case(
        "premise-fixed",
        answer=(
            "Запрет не полный. Согласно пункту 11.4, обгон запрещён на "
            "нерегулируемых перекрестках лишь при движении по дороге, не "
            "являющейся главной."
        ),
        context="обгон запрещен ... не являющейся главной",
        expected_keywords=["главн", "нерегулируем"],
        expected_context=["не являющейся главной"],
        forbidden_keywords=["полностью запрещ"],
    )
    assert sc.passed


def test_forbidden_list_is_optional_and_ignores_yo():
    from evals.scoring import score_case

    base = dict(answer="ответ", context="контекст",
                expected_keywords=["ответ"], expected_context=["контекст"])
    assert score_case("no-list", **base).passed
    # «ё» и «е» здесь тоже равнозначны, как и в остальных сверках
    sc = score_case("yo", answer="запрещён полностью", context="к",
                    expected_keywords=[], expected_context=["к"],
                    forbidden_keywords=["запрещен полностью"])
    assert not sc.passed


# --- Запрет не должен срабатывать на опровержении --------------------------
# Обе строки ниже — настоящие ответы системы из прогонов 04.09.2026 на один и
# тот же вопрос «почему ПДД полностью запрещают обгон на перекрёстках».


BAD_ANSWER = (
    "ПДД полностью запрещают обгон на перекрёстках, так как на регулируемых "
    "перекрестках условия для безопасного обгона особенно затруднены наличием "
    "сигналов светофора, а на нерегулируемых обгон запрещён, если дорога не "
    "является главной."
)

GOOD_ANSWER = (
    "В контексте указано, что обгон запрещён на регулируемых перекрестках, а "
    "также на нерегулируемых перекрестках при движении по дороге, не являющейся "
    "главной (пункт 11.4). Однако нельзя однозначно утверждать, что ПДД "
    "полностью запрещают обгон на всех типах перекрёстков."
)


def test_forbidden_phrase_flags_a_real_confirmation():
    from evals.scoring import present

    assert present(BAD_ANSWER, ["полностью запрещ"]) == ["полностью запрещ"]


def test_forbidden_phrase_ignores_the_same_words_inside_a_denial():
    from evals.scoring import present

    assert present(GOOD_ANSWER, ["полностью запрещ"]) == []


def test_denial_answer_now_passes_scoring():
    from evals.scoring import score_case

    sc = score_case(
        "premise",
        answer=GOOD_ANSWER,
        context="обгон запрещен ... не являющейся главной",
        expected_keywords=["главн", "нерегулируем"],
        expected_context=["не являющейся главной"],
        forbidden_keywords=["полностью запрещ"],
    )
    assert sc.passed, "опровержение ложной предпосылки — это верный ответ"


def test_confirmation_answer_still_fails_scoring():
    from evals.scoring import score_case

    sc = score_case(
        "premise",
        answer=BAD_ANSWER,
        context="обгон запрещен ... не являющейся главной",
        expected_keywords=["главн", "нерегулируем"],
        expected_context=["не являющейся главной"],
        forbidden_keywords=["полностью запрещ"],
    )
    assert not sc.passed


# --- Отрицание: сильное против слабого ------------------------------------
#
# «Не рекомендовано лечение высокими дозами» отрицает рекомендацию, а слова
# «высокими дозами» при этом утверждаются. Если они пришли из инструкции к
# другому препарату, это и есть ошибка, ради которой заведён forbidden.

CONTAMINATED = (
    "Курс лечения Нейробионом не всегда ограничен четырьмя неделями. "
    "В инструкции указано, что не рекомендовано лечение высокими дозами "
    "препарата более 4 недель."
)


def test_weak_negation_does_not_shield_a_borrowed_qualifier():
    from evals.scoring import present

    assert present(CONTAMINATED, ["высокими дозами"]) == ["высокими дозами"]


def test_strong_negation_still_shields_a_refutation():
    from evals.scoring import present

    refutation = "Нельзя однозначно утверждать, что ПДД полностью запрещают обгон."
    assert present(refutation, ["ПДД полностью запрещают"]) == []


def test_weak_negation_still_shields_an_adjacent_word():
    from evals.scoring import present

    assert present("Препарат не показан детям", ["показан детям"]) == []


# --- Честный отказ своими словами тоже отказ ------------------------------
#
# Лучший отказ не молчит, а называет, чего именно нет, и часто ссылается на
# область применения документа. Харнесс знал только казённые формулировки
# продукта и такие ответы засчитывал в провалы (замер 04.09.2026, СП 60).

def test_refusal_in_the_models_own_words_counts_as_a_refusal():
    from evals.scoring import looks_like_refusal

    assert looks_like_refusal(
        "Требования свода правил не распространяются на такие объекты. "
        "Конкретных значений воздухообмена в представленном контексте нет."
    )
    assert looks_like_refusal("Нет конкретной информации о расчётной температуре.")


def test_a_real_answer_is_not_read_as_a_refusal():
    from evals.scoring import looks_like_refusal

    assert not looks_like_refusal("Удельная энтальпия для Москвы — 57,8 кДж/кг.")
    assert not looks_like_refusal("Температура теплоносителя — не более 95 °C.")


# --- Отказ не индульгенция ------------------------------------------------
#
# Ответ может отказаться назвать число и соврать по дороге. Замер 04.09.2026
# на СП 60, вопрос про рентгеновский кабинет: первая половина переворачивает
# п. 1.2 («распространяются» вместо «НЕ распространяется»), вторая - честный
# отказ. Такой ответ получал PASS трижды подряд: проверка видела отказ и
# выходила, не дочитав. Мерилось «отказался ли», но не «не соврал ли,
# отказываясь», - и ложь проходила именно потому, что стояла рядом с
# правильным поведением.

_SCOPE_FORBIDDEN = ["распространяются на сооружения", "распространяется на сооружения"]

_INVERTED = (
    "В документе указано, что требования настоящего свода правил распространяются "
    "на сооружения, предназначенные для работ с радиоактивными веществами. Однако "
    "конкретной информации о том, какой воздухообмен закладывать, нет."
)

_HONEST = (
    "В документе сказано, что требования настоящего свода правил не распространяются "
    "на сооружения, предназначенные для работ с радиоактивными веществами. Однако "
    "конкретных указаний по воздухообмену в представленном контексте нет."
)


def test_refusal_wrapped_around_a_false_claim_fails():
    from evals.scoring import score_case

    sc = score_case(
        "gap", answer=_INVERTED, context="", expected_keywords=[], expected_context=[],
        expect_refusal=True, forbidden_keywords=_SCOPE_FORBIDDEN,
    )
    assert not sc.passed


def test_honest_refusal_still_passes():
    from evals.scoring import score_case

    sc = score_case(
        "gap", answer=_HONEST, context="", expected_keywords=[], expected_context=[],
        expect_refusal=True, forbidden_keywords=_SCOPE_FORBIDDEN,
    )
    assert sc.passed


# --- Повторные прогоны ----------------------------------------------------
#
# Один прогон на вопрос ловит поломку, но не измеряет улучшение. Замер
# 04.09.2026: правка чтения таблиц изменила вопрос с 1 верного ответа из 8 на
# 8 из 8, а набор с одним прогоном показал до и после одинаковые 19 из 26 -
# улучшения не было видно вообще, и правку можно было счесть бесполезной.

def _score(passed: bool) -> "CaseScore":
    from evals.scoring import CaseScore

    return CaseScore("c", None, None, None, passed)


def test_case_passes_only_when_every_run_passes():
    """Ответ, верный пять раз из восьми, доверия не заслуживает."""
    agg = aggregate_repeated([[_score(True)] * 4, [_score(True), _score(False)] * 2])
    assert agg.n == 2
    assert agg.passed == 1


def test_stability_sees_what_pass_rate_does_not():
    """Тот самый случай: 1 из 8 против 8 из 8 при одинаковом «не прошёл»."""
    before = aggregate_repeated([[_score(True)] + [_score(False)] * 7])
    after = aggregate_repeated([[_score(True)] * 7 + [_score(False)]])

    # Оба вопроса прошли не все прогоны, поэтому pass_rate одинаковый.
    assert before.pass_rate == after.pass_rate == 0.0
    # А устойчивость показывает разницу, ради которой всё и затевалось.
    assert before.stability < after.stability


def test_unstable_cases_are_counted_separately():
    agg = aggregate_repeated([
        [_score(True)] * 3,                       # работает
        [_score(False)] * 3,                      # сломано
        [_score(True), _score(False), _score(True)],  # как повезёт
    ])
    assert agg.unstable == 1


def test_single_run_behaves_exactly_as_before():
    agg = aggregate_repeated([[_score(True)], [_score(False)]])
    assert agg.repeats == 1
    assert agg.passed == 1
    assert agg.pass_rate == 0.5
    assert agg.unstable == 0


def test_report_shows_the_failing_run_not_the_lucky_one():
    """Разбираться надо с тем, что сломалось: удачный прогон рядом сбивает."""
    from evals.runner import CaseRun

    case = EvalCase(id="c1", question="?")
    run = CaseRun(
        case=case,
        answers=["верный", "неверный"],
        contexts=["a", "b"],
        scores=[_score(True), _score(False)],
    )
    assert run.passes == 1 and run.repeats == 2 and run.unstable
    assert run.answer == "неверный"
    assert run.context == "b"
