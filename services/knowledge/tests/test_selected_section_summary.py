from src.summary_engine import generate_direct_topic_summary, generate_selected_section_summary

# Stage 6e: ``workspace_id`` is required at every call site. These tests use
# FakeKB that doesn't care about isolation, so a single shared constant is
# enough.
TEST_WORKSPACE_ID = "ws-section-test"


class FakeKnowledgeBase:
    def __init__(self):
        self.used_search = False
        self.used_full_section = False

    def search_chunks_for_summary(self, query, file_filter="all", section_filter=None, top_k=None, workspace_id=None):
        self.used_search = True
        return [
            {
                "text": "Теоретические основы передачи данных включают ряды Фурье и полосу пропускания.",
                "source_file": "networks.pdf",
                "section": section_filter,
                "chunk_id": 1,
                "score": 1.0,
            }
        ]

    def get_file_chunks(self, file_filter="all", section_filter=None, workspace_id=None):
        self.used_full_section = True
        return [
            {
                "text": "Вся глава целиком.",
                "source_file": "networks.pdf",
                "section": section_filter,
                "chunk_id": 1,
            }
        ]


class FakeLLM:
    def __init__(self):
        self.calls = 0
        self.last_prompt = ""

    def call(self, prompt, max_tokens=None, temperature=None):
        self.calls += 1
        self.last_prompt = prompt
        return "Конспект по найденным фрагментам."


def test_selected_section_with_topic_uses_search_inside_section():
    kb = FakeKnowledgeBase()
    llm = FakeLLM()

    result = generate_selected_section_summary(
        kb=kb,
        llm=llm,
        selected_file="networks.pdf",
        section_filter="Глава 2",
        topic="Теоретические основы передачи данных",
        summary_type="Средний",
        workspace_id=TEST_WORKSPACE_ID,
    )

    assert kb.used_search
    assert not kb.used_full_section
    assert llm.calls == 1
    # Stage 7f removed the metadata header; assert the summary body is returned
    # instead. The "search inside section" behaviour is covered above.
    assert "Конспект по найденным фрагментам." in result


class RankedFakeKnowledgeBase(FakeKnowledgeBase):
    def search_chunks_for_summary(self, query, file_filter="all", section_filter=None, top_k=None, workspace_id=None):
        self.used_search = True
        return [
            {
                "text": "IEEE 802.11 описывает беспроводные локальные сети и точки доступа.",
                "source_file": "networks.pdf",
                "section": section_filter,
                "chunk_id": 10,
                "score": 0.5,
            },
            {
                "text": "4.5. Широкополосные беспроводные сети: WiMAX, стандарт 802.16, OFDM и MIMO.",
                "source_file": "networks.pdf",
                "section": section_filter,
                "chunk_id": 90,
                "score": 12.0,
            },
        ]


def test_selected_section_with_topic_sends_most_relevant_chunks_first():
    kb = RankedFakeKnowledgeBase()
    llm = FakeLLM()

    generate_selected_section_summary(
        kb=kb,
        llm=llm,
        selected_file="networks.pdf",
        section_filter="Глава 4",
        topic="Широкополосные беспроводные сети",
        summary_type="Средний",
        workspace_id=TEST_WORKSPACE_ID,
    )

    assert llm.last_prompt.index("WiMAX") < llm.last_prompt.index("IEEE 802.11")


def test_direct_topic_summary_sends_most_relevant_chunks_first():
    kb = RankedFakeKnowledgeBase()
    llm = FakeLLM()

    result = generate_direct_topic_summary(
        kb=kb,
        llm=llm,
        topic="Широкополосные беспроводные сети",
        summary_type="Средний",
        file_filter="networks.pdf",
        workspace_id=TEST_WORKSPACE_ID,
    )

    # Stage 7f removed the metadata header ("Режим: ..."); assert the body is
    # returned. Relevance ordering is asserted below.
    assert "Конспект по найденным фрагментам." in result
    assert llm.calls == 1
    assert llm.last_prompt.index("WiMAX") < llm.last_prompt.index("IEEE 802.11")


class NoisyDirectFakeKnowledgeBase(FakeKnowledgeBase):
    def search_chunks_for_summary(self, query, file_filter="all", section_filter=None, top_k=None, workspace_id=None):
        self.used_search = True
        return [
            {
                "text": "Глава 1 общий обзор беспроводных приложений без WiMAX.",
                "source_file": "networks.pdf",
                "section": "Глава 1",
                "chunk_id": 5,
                "score": 1.0,
            },
            {
                "text": "Широкополосные беспроводные сети используют WiMAX и стандарт 802.16.",
                "source_file": "networks.pdf",
                "section": "Глава 4",
                "chunk_id": 90,
                "score": 12.0,
            },
            {
                "text": "Архитектура 802.16 включает базовые станции и абонентские станции.",
                "source_file": "networks.pdf",
                "section": "Глава 4",
                "chunk_id": 91,
                "score": 10.0,
            },
            {
                "text": "Физический уровень WiMAX использует OFDM, OFDMA, TDD и FDD.",
                "source_file": "networks.pdf",
                "section": "Глава 4",
                "chunk_id": 92,
                "score": 9.0,
            },
            {
                "text": "DFS и регулирование мощности передатчика помогают сетям 802.11 избегать радаров в диапазоне 5 ГГц.",
                "source_file": "networks.pdf",
                "section": "Глава 4",
                "chunk_id": 93,
                "score": 8.0,
            },
            {
                "text": "Табличный технический хвост с числами.",
                "source_file": "networks.pdf",
                "section": "RTO = SRTT + 4 × RTTV AR.",
                "chunk_id": 300,
                "score": 0.5,
            },
        ]


def test_direct_topic_summary_focuses_on_primary_section():
    kb = NoisyDirectFakeKnowledgeBase()
    llm = FakeLLM()

    result = generate_direct_topic_summary(
        kb=kb,
        llm=llm,
        topic="Широкополосные беспроводные сети",
        summary_type="Средний",
        file_filter="networks.pdf",
        workspace_id=TEST_WORKSPACE_ID,
    )

    # Stage 7f removed the "Разделы источников" header line; the primary-section
    # focusing is verified by the prompt-content assertions below (noise chunks
    # excluded, Глава 4 / 802.16 content kept).
    assert "Конспект по найденным фрагментам." in result
    assert "Глава 1 общий обзор" not in llm.last_prompt
    assert "DFS" not in llm.last_prompt
    assert "Табличный технический хвост" not in llm.last_prompt
    assert "802.16" in llm.last_prompt


class SelectedSectionNoisyFakeKnowledgeBase(FakeKnowledgeBase):
    def search_chunks_for_summary(self, query, file_filter="all", section_filter=None, top_k=None, workspace_id=None):
        self.used_search = True
        return [
            {
                "text": "Теоретические основы передачи данных включают полосу пропускания канала.",
                "source_file": "networks.pdf",
                "section": section_filter,
                "chunk_id": 10,
                "score": 12.0,
            },
            {
                "text": "В теоретических основах передачи данных используется ряд Фурье и гармоники сигнала.",
                "source_file": "networks.pdf",
                "section": section_filter,
                "chunk_id": 11,
                "score": 11.0,
            },
            {
                "text": "Модель Найквиста задает максимальную скорость 2B log2 V.",
                "source_file": "networks.pdf",
                "section": section_filter,
                "chunk_id": 12,
                "score": 10.0,
            },
            {
                "text": "Перестройка частоты относится к методам передачи в широкой полосе.",
                "source_file": "networks.pdf",
                "section": section_filter,
                "chunk_id": 80,
                "score": 9.0,
            },
        ]


def test_selected_section_with_topic_filters_neighbor_chunks_by_anchors():
    kb = SelectedSectionNoisyFakeKnowledgeBase()
    llm = FakeLLM()

    generate_selected_section_summary(
        kb=kb,
        llm=llm,
        selected_file="networks.pdf",
        section_filter="Глава 2",
        topic="Теоретические основы передачи данных",
        summary_type="Средний",
        workspace_id=TEST_WORKSPACE_ID,
    )

    assert "полосу пропускания" in llm.last_prompt
    assert "ряд Фурье" in llm.last_prompt
    assert "Перестройка частоты" not in llm.last_prompt
