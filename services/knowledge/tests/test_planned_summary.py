from src.summary_engine import (
    build_extractive_short_summary,
    build_topic_search_plan,
    _chunks_from_matching_sections,
    _extract_short_points,
    _format_group_sources,
    generate_planned_topic_summary,
    retrieve_chunk_groups_by_plan,
    retrieve_chunks_by_plan,
)

# Stage 6e: KB/summary methods require ``workspace_id`` explicitly. These
# tests don't care about cross-workspace isolation (FakeKB ignores it), so
# a single test-wide constant keeps the call sites readable.
TEST_WORKSPACE_ID = "ws-summary-test"


class FakeLLM:
    def __init__(self, answer):
        self.answer = answer
        self.last_prompt = ""

    def call(self, prompt, max_tokens=500):
        self.last_prompt = prompt
        return self.answer


class FakeKnowledgeBase:
    def __init__(self):
        self.top_k_values = []

    def search_chunks_for_summary(self, query, file_filter="all", section_filter=None, top_k=None, workspace_id=None):
        self.top_k_values.append(top_k)
        query_index = len(self.top_k_values)

        return [
            {
                "text": f"{query} chunk {i}",
                "source_file": "book.pdf",
                "section": f"section-{query_index}-{i}",
                "chunk_id": query_index * 1000 + i,
                "score": 1.0,
            }
            for i in range(top_k)
        ]


class MappingKnowledgeBase:
    def __init__(self, mapping):
        self.mapping = mapping

    def search_chunks_for_summary(self, query, file_filter="all", section_filter=None, top_k=None, workspace_id=None):
        result = list(self.mapping.get(query, []))
        return result[:top_k]


class SectionKnowledgeBase:
    def __init__(self):
        self.search_calls = 0
        self.sections = [
            "ГЛАВА 7. § 34. МЕЖДУНАРОДНОЕ ПОЛОЖЕНИЕ РОССИИ К НАЧАЛУ XVIII в. СЕВЕРНАЯ ВОЙ НА",
            "ГЛАВА 16. ВЕЛИКАЯ ОТЕЧЕСТВЕННАЯ ВОЙ НА",
        ]

    def get_available_sections(self, workspace_id=None):
        return self.sections

    def get_file_chunks(self, file_filter="all", section_filter=None, workspace_id=None):
        return [
            {
                "text": f"{section_filter} text about topic.",
                "source_file": "book.pdf",
                "section": section_filter,
                "chunk_id": 1,
                "score": 1.0,
            }
        ]

    def search_chunks_for_summary(self, query, file_filter="all", section_filter=None, top_k=None, workspace_id=None):
        self.search_calls += 1
        return []


class PostwarSectionKnowledgeBase(SectionKnowledgeBase):
    def __init__(self):
        self.search_calls = 0
        self.sections = [
            "ГЛАВА 2. § 11. ВНЕШНЯЯ ПОЛИТИКА И МЕЖДУНАРОДНЫЕ СВЯЗИ",
            "ГЛАВА 17. ПОСЛЕВОЕННОЕ ВОССТАНОВЛЕНИЕ И ХОЛОДНАЯ ВОЙНА",
        ]


class NinetiesSectionKnowledgeBase(SectionKnowledgeBase):
    def __init__(self):
        self.search_calls = 0
        self.sections = [
            "ГЛАВА 19. § 100. ВНЕШНЯЯ ПОЛИТИКА СССР ПЕРИОДА ПЕРЕСТРОЙКИ",
            "ГЛАВА 19. § 101. РАСПАД СССР",
            "ГЛАВА 20. РОССИЯ 1990-Х ГОДОВ",
        ]


class NepSectionKnowledgeBase(SectionKnowledgeBase):
    def __init__(self):
        self.search_calls = 0
        self.sections = [
            "ГЛАВА 14. § 72. ГОРОД И ДЕРЕВНЯ В 1920-Х ГОДАХ. ПОЛИТИЧЕСКАЯ БОРЬБА В ПЕРИОД НЭПА",
        ]

    def get_file_chunks(self, file_filter="all", section_filter=None, workspace_id=None):
        return [
            {
                "text": "Политика коренизации нацеливалась на выдвижение местных кадров.",
                "source_file": "history.pdf",
                "section": section_filter,
                "chunk_id": 1,
                "score": 1.0,
            },
            {
                "text": "НЭП заменил продразверстку продналогом и допустил частную торговлю.",
                "source_file": "history.pdf",
                "section": section_filter,
                "chunk_id": 2,
                "score": 1.0,
            },
            {
                "text": "Новая экономическая политика помогала преодолеть послевоенную разруху.",
                "source_file": "history.pdf",
                "section": section_filter,
                "chunk_id": 3,
                "score": 1.0,
            },
        ]


class NepTocSectionKnowledgeBase(NepSectionKnowledgeBase):
    def get_file_chunks(self, file_filter="all", section_filter=None, workspace_id=None):
        return [
            {
                "text": (
                    "§ 72. Город и деревня в 1920-х годах. Политическая борьба "
                    "в период НЭПа ................................... 311\n"
                    "§ 73. Советское общество в 1920-х годах ............. 315\n"
                    "Глава 15. СССР В 1930-х ГОДАХ ...................... 319"
                ),
                "source_file": "history.pdf",
                "section": section_filter,
                "chunk_id": 1,
                "score": 1.0,
            },
            {
                "text": "НЭП заменил продразверстку продналогом и допустил частную торговлю.",
                "source_file": "history.pdf",
                "section": section_filter,
                "chunk_id": 2,
                "score": 1.0,
            },
        ]


def test_planned_retrieval_scales_per_query_for_detailed_context():
    kb = FakeKnowledgeBase()
    plan = ["period 1", "period 2", "period 3", "period 4"]

    chunks = retrieve_chunks_by_plan(
        kb=kb,
        topic="wide topic",
        plan=plan,
        target_chunks=80,
        workspace_id=TEST_WORKSPACE_ID,
    )

    assert len(chunks) == 80
    assert all(top_k > 5 for top_k in kb.top_k_values)


def test_planned_retrieval_keeps_default_limit_for_medium_context():
    kb = FakeKnowledgeBase()
    plan = ["part 1", "part 2", "part 3", "part 4"]

    chunks = retrieve_chunks_by_plan(
        kb=kb,
        topic="medium topic",
        plan=plan,
        workspace_id=TEST_WORKSPACE_ID,
    )

    assert len(chunks) == 40


def test_planned_retrieval_preserves_late_plan_periods_under_limit():
    topic = "Россия после Николая II до 2000 года"
    plan = [
        "революция 1917 года и смена политической власти",
        "Гражданская война и формирование советской власти",
        "перестройка, распад СССР и Россия 1990-х годов",
    ]

    mapping = {
        f"{topic}. {plan[0]}": [
            {
                "text": f"early {i}",
                "source_file": "book.pdf",
                "section": "Глава 10",
                "chunk_id": i,
                "score": 1.0,
            }
            for i in range(12)
        ],
        f"{topic}. {plan[1]}": [
            {
                "text": f"civil {i}",
                "source_file": "book.pdf",
                "section": "Глава 11",
                "chunk_id": 100 + i,
                "score": 1.0,
            }
            for i in range(12)
        ],
        f"{topic}. {plan[2]}": [
            {
                "text": f"late {i}",
                "source_file": "book.pdf",
                "section": "Глава 22",
                "chunk_id": 200 + i,
                "score": 1.0,
            }
            for i in range(6)
        ],
    }

    kb = MappingKnowledgeBase(mapping)

    chunks = retrieve_chunks_by_plan(
        kb=kb,
        topic=topic,
        plan=plan,
        target_chunks=40,
        workspace_id=TEST_WORKSPACE_ID,
    )

    texts = [chunk["text"] for chunk in chunks]

    assert any(text.startswith("late ") for text in texts)


def test_planned_retrieval_uses_plain_plan_query_before_combined_query():
    topic = "Россия после Николая II до 2000 года"
    item = "Гражданская война и формирование советской власти"

    mapping = {
        item: [
            {
                "text": "plain civil war chunk",
                "source_file": "book.pdf",
                "section": "Глава 12",
                "chunk_id": 1,
                "score": 1.0,
            }
        ],
        f"{topic}. {item}": [
            {
                "text": "combined civil war chunk",
                "source_file": "book.pdf",
                "section": "Глава 12",
                "chunk_id": 2,
                "score": 1.0,
            }
        ],
    }

    kb = MappingKnowledgeBase(mapping)

    chunks = retrieve_chunks_by_plan(
        kb=kb,
        topic=topic,
        plan=[topic, item],
        target_chunks=40,
        workspace_id=TEST_WORKSPACE_ID,
    )

    texts = [chunk["text"] for chunk in chunks]

    assert "plain civil war chunk" in texts


def test_grouped_retrieval_keeps_chunks_for_each_plan_item():
    topic = "Россия после Николая II до 2000 года"
    early = "революция 1917 года и смена политической власти"
    late = "перестройка, распад СССР и Россия 1990-х годов"

    mapping = {
        early: [
            {
                "text": f"early chunk {i}",
                "source_file": "book.pdf",
                "section": "Глава 10",
                "chunk_id": i,
                "score": 1.0,
            }
            for i in range(30)
        ],
        late: [
            {
                "text": "late reform chunk",
                "source_file": "book.pdf",
                "section": "Глава 22",
                "chunk_id": 200,
                "score": 1.0,
            }
        ],
    }

    kb = MappingKnowledgeBase(mapping)

    groups = retrieve_chunk_groups_by_plan(
        kb=kb,
        topic=topic,
        plan=[topic, early, late],
        target_chunks=40,
        workspace_id=TEST_WORKSPACE_ID,
    )

    chunks_by_item = {
        group["item"]: [chunk["text"] for chunk in group["chunks"]]
        for group in groups
    }

    assert "late reform chunk" in chunks_by_item[late]


def test_grouped_retrieval_uses_semantic_search_for_non_history_fast_modes():
    topic = "Широкополосные беспроводные сети"
    item = f"основные понятия темы: {topic}"
    kb = MappingKnowledgeBase({
        item: [
            {
                "text": "WiMAX и стандарт 802.16 обеспечивают широкополосный беспроводной доступ.",
                "source_file": "networks.pdf",
                "section": "Глава 4",
                "chunk_id": 42,
                "score": 10.0,
            }
        ]
    })

    groups = retrieve_chunk_groups_by_plan(
        kb=kb,
        topic=topic,
        plan=[topic, item],
        target_chunks=8,
        workspace_id=TEST_WORKSPACE_ID,
    )

    assert groups[0]["chunks"]
    assert "802.16" in groups[0]["chunks"][0]["text"]


def test_section_match_for_vov_avoids_northern_war():
    kb = SectionKnowledgeBase()

    chunks = _chunks_from_matching_sections(
        kb=kb,
        item="Великая Отечественная война и советский тыл",
        limit=2,
        workspace_id=TEST_WORKSPACE_ID,
    )

    assert chunks
    assert all("СЕВЕРНАЯ" not in chunk["section"] for chunk in chunks)
    assert any("ВЕЛИКАЯ ОТЕЧЕСТВЕННАЯ" in chunk["section"] for chunk in chunks)


def test_postwar_section_match_avoids_generic_foreign_policy():
    kb = PostwarSectionKnowledgeBase()

    chunks = _chunks_from_matching_sections(
        kb=kb,
        item="послевоенное восстановление, холодная война и внешняя политика СССР",
        limit=2,
        workspace_id=TEST_WORKSPACE_ID,
    )

    assert chunks
    assert all("ГЛАВА 2" not in chunk["section"] for chunk in chunks)
    assert any("ПОСЛЕВОЕННОЕ" in chunk["section"] for chunk in chunks)


def test_nineties_section_match_prefers_russia_1990s_chapter():
    kb = NinetiesSectionKnowledgeBase()

    chunks = _chunks_from_matching_sections(
        kb=kb,
        item="перестройка, распад СССР и Россия 1990-х годов",
        limit=3,
        workspace_id=TEST_WORKSPACE_ID,
    )

    assert chunks
    sections = [chunk["section"] for chunk in chunks]

    assert any("РОССИЯ 1990" in section for section in sections)
    assert any("РАСПАД СССР" in section or "ПЕРЕСТРОЙКИ" in section for section in sections)


def test_nep_section_match_prefers_nep_content_over_shifted_intro():
    kb = NepSectionKnowledgeBase()

    chunks = _chunks_from_matching_sections(
        kb=kb,
        item="создание СССР, НЭП и экономическая политика 1920-х годов",
        limit=2,
        workspace_id=TEST_WORKSPACE_ID,
    )

    texts = " ".join(chunk["text"] for chunk in chunks)

    assert "НЭП" in texts
    assert "продналог" in texts
    assert "коренизации" not in texts


def test_section_match_filters_table_of_contents_chunks():
    kb = NepTocSectionKnowledgeBase()

    chunks = _chunks_from_matching_sections(
        kb=kb,
        item="создание СССР, НЭП и экономическая политика 1920-х годов",
        limit=2,
        workspace_id=TEST_WORKSPACE_ID,
    )

    texts = " ".join(chunk["text"] for chunk in chunks)

    assert "продналогом" in texts
    assert "Глава 15" not in texts
    assert "................................" not in texts


def test_format_group_sources_keeps_long_real_chapter_heading():
    section = "ГЛАВА 14. § 72. ГОРОД И ДЕРЕВНЯ В 1920-Х ГОДАХ. ПОЛИТИЧЕСКАЯ БОРЬБА В ПЕРИОД НЭПА"

    result = _format_group_sources([
        {
            "text": "НЭП",
            "source_file": "history.pdf",
            "section": section,
            "chunk_id": 1,
        }
    ])

    assert "НЭП" in result


def test_short_grouped_retrieval_uses_sections_without_semantic_search():
    kb = SectionKnowledgeBase()

    groups = retrieve_chunk_groups_by_plan(
        kb=kb,
        topic="Россия после Николая II до 2000 года",
        plan=[
            "Россия после Николая II до 2000 года",
            "Великая Отечественная война",
        ],
        target_chunks=8,
        workspace_id=TEST_WORKSPACE_ID,
    )

    assert kb.search_calls == 0
    assert groups[0]["chunks"]


def test_medium_grouped_retrieval_skips_semantic_backup_for_composite_history_item():
    kb = SectionKnowledgeBase()

    groups = retrieve_chunk_groups_by_plan(
        kb=kb,
        topic="Россия после Николая II до 2000 года",
        plan=[
            "Россия после Николая II до 2000 года",
            "перестройка, распад СССР и Россия 1990-х годов",
        ],
        target_chunks=18,
        workspace_id=TEST_WORKSPACE_ID,
    )

    assert kb.search_calls == 0
    assert groups


def test_short_grouped_retrieval_skips_semantic_backup_for_composite_history_item():
    kb = SectionKnowledgeBase()

    retrieve_chunk_groups_by_plan(
        kb=kb,
        topic="Россия после Николая II до 2000 года",
        plan=[
            "Россия после Николая II до 2000 года",
            "перестройка, распад СССР и Россия 1990-х годов",
        ],
        target_chunks=8,
        workspace_id=TEST_WORKSPACE_ID,
    )

    assert kb.search_calls == 0


def test_extractive_short_summary_does_not_need_llm():
    result = build_extractive_short_summary(
        topic="Россия после Николая II до 2000 года",
        plan=["Гражданская война"],
        chunk_groups=[
            {
                "item": "Гражданская война",
                "chunks": [
                    {
                        "text": (
                            "[ГЛАВА 13]\n"
                            "Гражданская война началась после Октябрьской революции "
                            "и сопровождалась борьбой красных и белых сил."
                        )
                    }
                ],
            }
        ],
    )

    assert "Гражданская война началась" in result
    assert "КРАТКИЙ ТЕМАТИЧЕСКИЙ КОНСПЕКТ" in result


def test_extractive_points_prefer_sentences_related_to_plan_item():
    points = _extract_short_points(
        chunks=[
            {
                "text": (
                    "Европейский концерт обеспечивал баланс сил великих держав. "
                    "В июне 1941 года началась Великая Отечественная война, "
                    "когда германские войска перешли границу СССР."
                )
            }
        ],
        item="Великая Отечественная война и советский тыл",
        max_points=1,
    )

    assert points == [
        "В июне 1941 года началась Великая Отечественная война, когда германские войска перешли границу СССР."
    ]


def test_extractive_points_skip_lowercase_pdf_fragments():
    points = _extract_short_points(
        chunks=[
            {
                "text": (
                    "мистов, составляли 14%, что является едва ли не самым большим "
                    "ежегодным приростом за всю историю нашей страны. "
                    "В результате за 1945–1950 гг. объемы производства тяжелой "
                    "промышленности удвоились."
                )
            }
        ],
        item="послевоенное восстановление, холодная война и внешняя политика СССР",
        max_points=1,
    )

    assert points == [
        "В результате за 1945–1950 гг. объемы производства тяжелой промышленности удвоились."
    ]


def test_extractive_points_clean_common_pdf_word_breaks():
    points = _extract_short_points(
        chunks=[
            {
                "text": (
                    "Великая Отечественная вой на (1941–1945) является составной "
                    "и важной частью Второй мировой вой ны (1939–1945)."
                )
            }
        ],
        item="Великая Отечественная война и советский тыл",
        max_points=1,
    )

    assert "вой на" not in points[0]
    assert "война" in points[0]


def test_extractive_points_clean_long_pdf_hyphen_breaks():
    points = _extract_short_points(
        chunks=[
            {
                "text": (
                    "Уже к концу 1914 г. стало очевидным, что довоенные запасы "
                    "исчерпываются быстрее, чем планировалось, а окончание военных "
                    "действий не про - сматривалось даже в среднесрочной перспективе."
                )
            }
        ],
        item="революция 1917 года и смена политической власти",
        max_points=1,
    )

    assert "про - сматривалось" not in points[0]
    assert "просматривалось" in points[0]


def test_extractive_points_skip_truncated_initial_tail():
    points = _extract_short_points(
        chunks=[
            {
                "text": (
                    "К концу апреля 1991 г., по данным Госкомстата РСФСР, "
                    "в России уже существовали 164 концерна, 92 консорциума, "
                    "1186 акционерных Е. "
                    "Ваучерная приватизация стала следующим этапом экономических реформ."
                )
            }
        ],
        item="перестройка, распад СССР и Россия 1990-х годов",
        max_points=1,
    )

    assert points == [
        "Ваучерная приватизация стала следующим этапом экономических реформ."
    ]


def test_extractive_points_rank_globally_across_chunks():
    points = _extract_short_points(
        chunks=[
            {
                "text": (
                    "Поэтому в последние годы утвердилась концепция Великой российской "
                    "революции 1917–1922 гг. Революционные события стали результатом "
                    "комплекса факторов."
                )
            },
            {
                "text": (
                    "В этой ситуации 3 марта 1918 г. Советская Россия была вынуждена "
                    "пойти на заключение невыгодного для нее Брестского мира."
                )
            },
        ],
        item="Гражданская война и формирование советской власти",
        max_points=1,
    )

    assert points == [
        "В этой ситуации 3 марта 1918 г. Советская Россия была вынуждена пойти на заключение невыгодного для нее Брестского мира."
    ]


def test_topic_plan_expands_when_llm_returns_only_original_topic():
    llm = FakeLLM("Россия после Николая II до 2000 года")

    plan = build_topic_search_plan(
        llm=llm,
        topic="Россия после Николая II до 2000 года",
        summary_type="Подробный",
        max_items=7,
    )

    assert len(plan) == 7
    assert plan[0] == "Россия после Николая II до 2000 года"
    assert any("революц" in item.lower() for item in plan)
    assert any("перестрой" in item.lower() or "1990" in item.lower() for item in plan)
    assert any("холодная война" in item.lower() for item in plan)
    assert not any("оттепель, застой" in item.lower() for item in plan)


def test_history_topic_uses_stable_chronological_plan_over_llm_plan():
    llm = FakeLLM(
        "Гражданская война в России (1918-1920)\n"
        "Репрессии и террор в СССР (1918-1922)\n"
        "Экономическая политика Советского государства (1917-1924)"
    )

    plan = build_topic_search_plan(
        llm=llm,
        topic="Россия после Николая II до 2000 года",
        summary_type="Краткий",
        max_items=7,
    )

    assert len(plan) == 7
    assert "Репрессии и террор в СССР (1918-1922)" not in plan
    assert any("Великая Отечественная" in item for item in plan)
    assert any("перестрой" in item.lower() or "1990" in item.lower() for item in plan)


def test_grouped_retrieval_respects_short_target_chunk_limit():
    kb = FakeKnowledgeBase()

    groups = retrieve_chunk_groups_by_plan(
        kb=kb,
        topic="wide topic",
        plan=["wide topic", "part 1", "part 2", "part 3"],
        target_chunks=18,
        workspace_id=TEST_WORKSPACE_ID,
    )

    assert sum(len(group["chunks"]) for group in groups) <= 18


class MediumPromptKnowledgeBase:
    def search_chunks_for_summary(self, query, file_filter="all", section_filter=None, top_k=None, workspace_id=None):
        return []

    def get_available_sections(self, workspace_id=None):
        return [
            "ГЛАВА 13. § 64. ПРИЧИНЫ РЕВОЛЮЦИОННОГО КРИЗИСА 1917 г.",
        ]

    def get_file_chunks(self, file_filter="all", section_filter=None, workspace_id=None):
        return [
            {
                "text": "Февральская революция привела к смене власти.",
                "source_file": "history.pdf",
                "section": section_filter,
                "chunk_id": 1,
                "score": 1.0,
            }
        ]


def test_medium_planned_prompt_does_not_use_original_topic_as_section():
    topic = "Россия после Николая II до 2000 года"
    llm = FakeLLM("Средний конспект")

    result = generate_planned_topic_summary(
        kb=MediumPromptKnowledgeBase(),
        llm=llm,
        topic=topic,
        summary_type="Средний",
        file_filter="history.pdf",
        workspace_id=TEST_WORKSPACE_ID,
    )

    plan_block = llm.last_prompt.split("НАЙДЕННЫЕ ФРАГМЕНТЫ ПО ПУНКТАМ:")[0]

    assert f"- {topic}" not in plan_block
    assert f"Пункт плана: {topic}" not in llm.last_prompt
    # Stage 7f removed the metadata header that echoed the topic; assert the
    # summary body is returned instead. The "topic not used as a section/plan
    # item" intent is covered by the assertions above and below.
    assert "Средний конспект" in result
    assert "Не меняй юридический или политический статус" in llm.last_prompt
    assert "Не объединяй разные даты и процессы" in llm.last_prompt
    assert "Не используй оценочные слова" in llm.last_prompt


def test_topic_plan_drops_truncated_items_and_uses_fallback_coverage():
    llm = FakeLLM(
        "Российская революция 1917 года\n"
        "Перестройка Горбачева и рас"
    )

    plan = build_topic_search_plan(
        llm=llm,
        topic="Россия после Николая II до 2000 года",
        summary_type="Средний",
        max_items=6,
    )

    assert all(not item.lower().endswith("рас") for item in plan)
    assert any("гражданская война" in item.lower() for item in plan)
    assert any("1990" in item.lower() or "распад ссср" in item.lower() for item in plan)
