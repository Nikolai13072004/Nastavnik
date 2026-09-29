"""Stage 2: tests for workspace-scoped storage/index isolation in KnowledgeBase.

These tests exercise the *real* ``KnowledgeBase`` against a throwaway ChromaDB
collection (created in ``tmp_path``). To avoid loading the heavy embedding /
reranker models in CI, the instance is built with ``__new__`` (skipping
``__init__``) and wired up with a tiny deterministic fake embedder.
"""
import hashlib

import chromadb
import pytest

import config
from src.knowledge_base import KnowledgeBase


class FakeEmbeddings:
    """Deterministic, dependency-free stand-in for the BGE-M3 embeddings."""

    def __init__(self, dim=16):
        self.dim = dim

    def _vec(self, text):
        digest = hashlib.md5(text.strip().encode("utf-8")).digest()
        repeats = (self.dim // len(digest)) + 1
        raw = (digest * repeats)[: self.dim]
        vec = [b / 255.0 for b in raw]
        norm = sum(v * v for v in vec) ** 0.5
        if norm == 0:
            return vec
        return [v / norm for v in vec]

    def embed_documents(self, texts):
        return [self._vec(text) for text in texts]

    def embed_query(self, text):
        return self._vec(text)


def make_kb(tmp_path):
    kb = KnowledgeBase.__new__(KnowledgeBase)
    kb._log = lambda *args, **kwargs: None
    kb._llm = None
    kb._reranker = None
    kb._reranker_loaded = True  # skip loading the cross-encoder
    kb._embeddings = FakeEmbeddings()

    client = chromadb.PersistentClient(path=str(tmp_path / "chromadb"))
    kb._client = client
    kb._col = client.create_collection(
        name=config.COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    return kb


def write_doc(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_add_book_rejects_near_empty(tmp_path):
    """Stage 39/40: a file with no extractable text fails with a clear message
    instead of indexing an empty material."""
    kb = make_kb(tmp_path)
    path = write_doc(tmp_path, "empty.txt", "   ")  # nothing usable

    result = kb.add_book(path, workspace_id="ws-x")

    assert "Не удалось извлечь текст" in result
    assert kb._col.count() == 0  # nothing indexed


def test_add_book_surfaces_ocr_unavailable_for_image(tmp_path, monkeypatch):
    """Stage 40: an image with no OCR binary on the server surfaces a friendly
    OCR message (not a stack trace) and indexes nothing."""
    from PIL import Image

    from src import ocr

    kb = make_kb(tmp_path)
    img_path = tmp_path / "scan.png"
    Image.new("RGB", (40, 20), "white").save(str(img_path))
    monkeypatch.setattr(ocr, "tesseract_available", lambda: False)

    result = kb.add_book(str(img_path), workspace_id="ws-x")

    assert "OCR" in result
    assert kb._col.count() == 0


def test_chunk_id_differs_across_workspaces_for_same_text():
    """Stage 3c: _chunk_id is now (workspace_id, document_id, text)."""
    same_text = "Один и тот же фрагмент текста для двух разных рабочих пространств."

    id_a = KnowledgeBase._chunk_id("workspace-a", "doc-1", same_text)
    id_b = KnowledgeBase._chunk_id("workspace-b", "doc-1", same_text)

    assert id_a != id_b
    # Stable for repeated calls with the same arguments.
    assert id_a == KnowledgeBase._chunk_id("workspace-a", "doc-1", same_text)


def test_chunk_id_differs_across_documents_for_same_text_in_one_workspace():
    """Replace-on-conflict relies on chunk_id changing when document_id does."""
    same_text = "Замена документа должна получить новые ID чанков."

    id_old = KnowledgeBase._chunk_id("workspace-a", "doc-old", same_text)
    id_new = KnowledgeBase._chunk_id("workspace-a", "doc-new", same_text)

    assert id_old != id_new


def test_add_book_stores_original_name_as_source_file(tmp_path):
    """Stage 3d fix: source_file metadata is the user-facing original_name.

    Authenticated uploads land on disk at
    ``docs/<workspace>/<document_id>__<original_name>``, but Chroma chunks
    must record ``source_file = original_name`` so:

    * ``list_materials`` can look up profile by ``Document.original_name``
      (which surfaced the smoke-test bug — KB returned 0 chunks because it
      was looking for ``alice_doc.txt`` while metadata held
      ``<uuid>__alice_doc.txt``);
    * chat source labels show ``alice_doc.txt -> Раздел`` instead of
      ``13606847-...__alice_doc.txt -> Раздел``.
    """
    kb = make_kb(tmp_path)
    text = "Содержимое для проверки имени source_file. " * 5

    document_id = "doc-uuid-1234"
    on_disk = write_doc(tmp_path, f"{document_id}__alice_doc.txt", text)

    kb.add_book(
        on_disk,
        workspace_id="workspace-a",
        document_id=document_id,
        original_name="alice_doc.txt",
    )

    # The KB profile look-up by original_name now returns non-zero counts.
    profile = kb.get_file_profile("alice_doc.txt", workspace_id="workspace-a")
    assert profile["chunk_count"] > 0

    # Files listing surfaces the user-facing name (no UUID prefix).
    files = kb.get_available_files(workspace_id="workspace-a")
    assert files == ["alice_doc.txt"]


def test_get_document_chunks_filters_workspace_and_document(tmp_path):
    kb = make_kb(tmp_path)
    first = write_doc(tmp_path, "first.txt", "Первый документ с проверяемой цитатой. " * 5)
    second = write_doc(tmp_path, "second.txt", "Второй документ с другим текстом. " * 5)
    kb.add_book(first, workspace_id="workspace-a", document_id="document-a")
    kb.add_book(second, workspace_id="workspace-a", document_id="document-b")
    kb.add_book(first, workspace_id="workspace-b", document_id="document-a")

    chunks = kb.get_document_chunks("document-a", workspace_id="workspace-a")

    assert chunks
    assert all(chunk["source_file"] == "first.txt" for chunk in chunks)
    assert any("проверяемой цитатой" in chunk["text"] for chunk in chunks)
    assert kb.get_document_chunks("document-b", workspace_id="workspace-b") == []


def test_add_book_falls_back_to_filename_when_original_name_missing(tmp_path):
    """Legacy/direct path: no ``original_name`` -> source_file is the
    on-disk basename (unchanged Stage 2 behaviour)."""
    kb = make_kb(tmp_path)
    on_disk = write_doc(tmp_path, "legacy_book.txt", "Минимальный материал. " * 5)

    kb.add_book(on_disk, workspace_id="workspace-legacy")

    files = kb.get_available_files(workspace_id="workspace-legacy")
    assert files == ["legacy_book.txt"]


def test_pdf_page_metadata_is_written_to_chunks(tmp_path, monkeypatch):
    kb = make_kb(tmp_path)
    path = write_doc(tmp_path, "manual.pdf", "placeholder")
    pages = [
        (1, "Правило первой страницы про запуск оборудования. " * 4),
        (2, "Правило второй страницы про безопасную остановку. " * 4),
    ]
    monkeypatch.setattr("src.knowledge_base.load_file", lambda _path: "\n\n".join(t for _, t in pages))
    monkeypatch.setattr("src.knowledge_base.last_load_pages", lambda: pages)

    result = kb.add_book(path, workspace_id="workspace-pages")
    stored = kb._col.get(where={"workspace_id": "workspace-pages"}, include=["metadatas"])
    page_ranges = {
        (meta.get("page_start"), meta.get("page_end"))
        for meta in stored["metadatas"]
    }

    assert result.startswith("✅")
    # Both deliberately short pages fit into one chunk, so the honest source
    # locator is a range rather than two artificial fragments.
    assert page_ranges == {(1, 2)}


def test_oversized_extracted_text_is_rejected_instead_of_partially_indexed(tmp_path, monkeypatch):
    kb = make_kb(tmp_path)
    path = write_doc(tmp_path, "huge.txt", "Исходный файл существует.")
    monkeypatch.setattr(config, "MAX_INDEX_CHARS", 50)
    monkeypatch.setattr("src.knowledge_base.load_file", lambda _path: "важный текст " * 20)
    monkeypatch.setattr("src.knowledge_base.last_load_pages", lambda: [])

    result = kb.add_book(path, workspace_id="workspace-limit")

    assert "не проиндексирован частично" in result
    assert kb._col.count() == 0


def test_add_book_same_text_indexed_independently_per_workspace(tmp_path):
    kb = make_kb(tmp_path)
    text = "Содержимое учебника одинаково для обоих пространств. " * 5

    doc_a = write_doc(tmp_path, "book_a.txt", text)
    doc_b = write_doc(tmp_path, "book_b.txt", text)

    result_a = kb.add_book(doc_a, workspace_id="workspace-a")
    result_b = kb.add_book(doc_b, workspace_id="workspace-b")

    assert result_a.startswith("✅")
    assert result_b.startswith("✅")

    stats_a = kb.stats(workspace_id="workspace-a")
    stats_b = kb.stats(workspace_id="workspace-b")

    assert stats_a["total_chunks"] > 0
    assert stats_b["total_chunks"] > 0
    # Indexing the same text in another workspace must not be skipped as a
    # "duplicate" of workspace-a's chunks.
    assert stats_a["total_chunks"] == stats_b["total_chunks"]


def test_listing_and_stats_are_scoped_to_workspace(tmp_path):
    kb = make_kb(tmp_path)

    doc_a = write_doc(tmp_path, "alpha.txt", "Материал рабочего пространства A. " * 5)
    doc_b = write_doc(tmp_path, "beta.txt", "Материал рабочего пространства B. " * 5)

    kb.add_book(doc_a, workspace_id="workspace-a")
    kb.add_book(doc_b, workspace_id="workspace-b")

    files_a = kb.get_available_files(workspace_id="workspace-a")
    files_b = kb.get_available_files(workspace_id="workspace-b")

    assert files_a == ["alpha.txt"]
    assert files_b == ["beta.txt"]

    stats_a = kb.stats(workspace_id="workspace-a")
    stats_b = kb.stats(workspace_id="workspace-b")

    assert stats_a["books"] == ["alpha.txt"]
    assert stats_b["books"] == ["beta.txt"]

    chunks_a = kb.get_file_chunks(workspace_id="workspace-a")
    chunks_b = kb.get_file_chunks(workspace_id="workspace-b")

    assert all(chunk["source_file"] == "alpha.txt" for chunk in chunks_a)
    assert all(chunk["source_file"] == "beta.txt" for chunk in chunks_b)


def test_search_with_sources_does_not_leak_across_workspaces(tmp_path):
    kb = make_kb(tmp_path)

    text_a = "Уникальный текст про рабочее пространство Альфа. " * 5
    text_b = "Уникальный текст про рабочее пространство Бета. " * 5

    doc_a = write_doc(tmp_path, "alpha.txt", text_a)
    doc_b = write_doc(tmp_path, "beta.txt", text_b)

    kb.add_book(doc_a, workspace_id="workspace-a")
    kb.add_book(doc_b, workspace_id="workspace-b")

    _, sources_a = kb.search_with_sources(text_a, workspace_id="workspace-a")
    _, sources_b = kb.search_with_sources(text_a, workspace_id="workspace-b")

    assert sources_a, "expected workspace-a search to find its own chunk"
    assert all(src["source_file"] == "alpha.txt" for src in sources_a)
    # Searching workspace-b for workspace-a's text must never surface
    # workspace-a's chunks.
    assert all(src["source_file"] != "alpha.txt" for src in sources_b)


def test_approved_document_ids_exclude_other_editions_before_retrieval(tmp_path):
    kb = make_kb(tmp_path)
    editions = [
        ("workspace-a", "approved", "Q250: отключить питание на 17 минут."),
        ("workspace-a", "revoked", "Q250: старое правило, ждать 2 минуты."),
        ("workspace-b", "approved", "Q250: чужое правило, ждать 99 минут."),
    ]
    for index, (workspace, document_id, text) in enumerate(editions):
        kb.add_book(
            write_doc(tmp_path, f"edition-{index}.txt", text * 5),
            workspace_id=workspace,
            document_id=document_id,
            original_name="policy.txt",
        )

    chunks = kb.get_file_chunks(file_filter=["approved"], workspace_id="workspace-a")
    assert chunks
    assert all("17 минут" in chunk["text"] for chunk in chunks)
    context, sources = kb.search_with_sources(
        "Сколько ждать перед заменой Q250?",
        file_filter=["approved"],
        workspace_id="workspace-a",
    )
    assert sources and "17 минут" in context
    assert "старое правило" not in context
    assert "чужое правило" not in context
    assert all("17 минут" in source["text"] for source in sources)
    assert kb.search_with_sources(
        "Q250", file_filter=["missing"], workspace_id="workspace-a",
    ) == ("", [])
    with pytest.raises(ValueError):
        kb.get_file_chunks(file_filter=[], workspace_id="workspace-a")


def test_remove_book_only_affects_its_own_workspace(tmp_path):
    kb = make_kb(tmp_path)

    text = "Общий текст материала, который потом удалят. " * 5
    doc_a = write_doc(tmp_path, "shared.txt", text)
    doc_b = write_doc(tmp_path, "shared.txt", text)

    kb.add_book(doc_a, workspace_id="workspace-a")
    kb.add_book(doc_b, workspace_id="workspace-b")

    kb.remove_book("shared.txt", workspace_id="workspace-a")

    assert kb.get_available_files(workspace_id="workspace-a") == []
    assert kb.get_available_files(workspace_id="workspace-b") == ["shared.txt"]


def test_clear_only_affects_its_own_workspace(tmp_path):
    kb = make_kb(tmp_path)

    doc_a = write_doc(tmp_path, "alpha.txt", "Материал A для очистки. " * 5)
    doc_b = write_doc(tmp_path, "beta.txt", "Материал B остаётся. " * 5)

    kb.add_book(doc_a, workspace_id="workspace-a")
    kb.add_book(doc_b, workspace_id="workspace-b")

    kb.clear(workspace_id="workspace-a")

    assert kb.get_available_files(workspace_id="workspace-a") == []
    assert kb.get_available_files(workspace_id="workspace-b") == ["beta.txt"]
    assert kb.stats(workspace_id="workspace-b")["total_chunks"] > 0


def test_search_chunks_for_summary_falls_back_to_workspace_chunks_when_pools_empty(tmp_path, monkeypatch):
    """Regression guard for the Stage 6 smoke bug.

    When semantic + lexical pools both come back empty (e.g. short English
    queries like "Wi-Fi" against a Russian-language scorer, or
    Chroma-version quirks where the keyword search yields nothing) the
    summary path used to return ``[]``, leaving the user with "тема не
    найдена" even though Assistant (which has its own fallback) found the
    same material. ``search_chunks_for_summary`` now mirrors
    ``search_with_sources`` and falls back to whatever chunks the
    workspace + file_filter combination owns.
    """
    kb = make_kb(tmp_path)
    text = (
        "Wi-Fi - стандарт беспроводных локальных сетей.\n"
        "Wi-Fi работает в полосе 2.4 GHz и 5 GHz.\n"
    )
    kb.add_book(
        write_doc(tmp_path, "wifi.txt", text),
        workspace_id="workspace-a",
        document_id="doc-wifi",
        original_name="wifi.txt",
    )

    # Force the regular semantic + lexical pipelines to return empty so
    # the test exercises the fallback alone. This is the user's
    # observed symptom, not a contrived state — semantic returning [] is
    # how the live bug manifests.
    monkeypatch.setattr(kb, "_raw_search", lambda *_a, **_kw: [])
    monkeypatch.setattr(
        kb,
        "_lexical_candidates_for_summary",
        lambda *_a, **_kw: [],
    )

    chunks = kb.search_chunks_for_summary(
        query="Wi-Fi",
        file_filter="all",
        section_filter=None,
        top_k=18,
        workspace_id="workspace-a",
    )

    assert chunks, "fallback must surface the workspace's chunks"
    assert all(c["source_file"] == "wifi.txt" for c in chunks)


def test_search_chunks_for_summary_fallback_stays_inside_caller_workspace(tmp_path, monkeypatch):
    """Belt-and-braces: the fallback must never leak chunks from another
    workspace. Stage 3+ isolation invariants apply to the fallback path
    too."""
    kb = make_kb(tmp_path)
    kb.add_book(
        write_doc(tmp_path, "alice.txt", "Документ Alice про Wi-Fi сети. " * 3),
        workspace_id="workspace-a",
        document_id="doc-alice",
        original_name="alice.txt",
    )
    kb.add_book(
        write_doc(tmp_path, "bob.txt", "Документ Bob про Bluetooth. " * 3),
        workspace_id="workspace-b",
        document_id="doc-bob",
        original_name="bob.txt",
    )

    monkeypatch.setattr(kb, "_raw_search", lambda *_a, **_kw: [])
    monkeypatch.setattr(
        kb,
        "_lexical_candidates_for_summary",
        lambda *_a, **_kw: [],
    )

    chunks_a = kb.search_chunks_for_summary(
        query="anything",
        file_filter="all",
        section_filter=None,
        top_k=18,
        workspace_id="workspace-a",
    )
    chunks_b = kb.search_chunks_for_summary(
        query="anything",
        file_filter="all",
        section_filter=None,
        top_k=18,
        workspace_id="workspace-b",
    )

    assert chunks_a and all(c["source_file"] == "alice.txt" for c in chunks_a)
    assert chunks_b and all(c["source_file"] == "bob.txt" for c in chunks_b)
    files_a = {c["source_file"] for c in chunks_a}
    files_b = {c["source_file"] for c in chunks_b}
    assert files_a.isdisjoint(files_b)


# --- Адресный поиск по номеру пункта --------------------------------------
#
# «Что требует пункт 6.2.13», «по 7.4.5», «согласно статье 91» - рабочий язык,
# которым на документ ссылаются в компании. Семантический поиск такой вопрос не
# решает: эмбеддинг номера ничем не ближе к нужному фрагменту, чем к любому
# другому. Замер 04.09.2026 на СП 60.13330.2020: пункт 6.2.13 лежит в базе, а
# вопрос про него получал отказ.

def test_point_refs_finds_addresses_in_a_question():
    from src.knowledge_base import KnowledgeBase as KB

    assert KB._point_refs("Что требует пункт 6.2.13?") == ["6.2.13"]
    assert KB._point_refs("какое требование в 7.11.13") == ["7.11.13"]
    assert KB._point_refs("Что в п. 5.3 написано?") == ["5.3"]
    assert KB._point_refs("Что сказано в статье 91 ТК?") == ["Статья 91", "статья 91"]


def test_point_refs_ignores_plain_numbers():
    """Обычный вопрос не должен уходить в адресную ветку вовсе."""
    from src.knowledge_base import KnowledgeBase as KB

    assert KB._point_refs("Какая температура теплоносителя допускается?") == []
    assert KB._point_refs("Скорость воды 1.5 м/с это много?") == []
    assert KB._point_refs("Сколько будет 2.5 плюс 3?") == []


def test_point_refs_drops_a_truncated_prefix():
    """«6.2» из вопроса про 6.2.13 - тот же адрес, только обрубленный.

    Отдельный поиск по нему совпал бы с десятком чужих пунктов и вытеснил
    настоящий из потолка _PINNED_TOTAL.
    """
    from src.knowledge_base import KnowledgeBase as KB

    assert KB._point_refs("Насколько по 6.2.13 закладывать поток?") == ["6.2.13"]


def test_exact_refs_finds_product_identifiers_without_plain_words_or_years():
    from src.knowledge_base import KnowledgeBase as KB

    assert KB._exact_refs("Что сказано про Q250 и ns-1041?") == [
        "Q250", "ns-1041", "NS-1041"
    ]
    assert KB._exact_refs("Что изменилось в регламенте за 2026 год?") == []


def test_exact_refs_expands_russian_rule_numbers_for_english_documents():
    from src.knowledge_base import KnowledgeBase as KB

    refs = KB._exact_refs("Как совместно применяются правила 602 и правило 603?")
    assert "Rule 602" in refs
    assert "Rule 603" in refs


def test_reranker_cannot_drop_strong_dense_and_lexical_candidates(monkeypatch):
    from src.knowledge_base import KnowledgeBase as KB

    kb = object.__new__(KB)
    kb._reranker_loaded = True

    class BadReranker:
        def predict(self, pairs):
            # Deliberately places the relevant first two candidates last.
            return [-100.0, -90.0] + list(range(len(pairs) - 2, 0, -1))

    kb._reranker = BadReranker()
    candidates = [
        ("NASA exact launch abort procedure", {}, 0.99),
        ("launch abort telemetry checklist", {}, 0.98),
    ] + [(f"unrelated candidate {i}", {}, 0.5 - i / 100) for i in range(20)]

    ranked = kb._rerank_candidates("NASA launch abort procedure", candidates)
    docs = [item[0] for item in ranked]
    assert "NASA exact launch abort procedure" in docs
    assert "launch abort telemetry checklist" in docs
    assert len(ranked) == config.RERANK_TOP_K


@pytest.mark.parametrize("has_reranker", [True, False])
def test_inflected_russian_match_with_and_without_reranker(has_reranker):
    from src.knowledge_base import KnowledgeBase as KB

    kb = object.__new__(KB)
    kb._reranker_loaded = True

    class BadReranker:
        def predict(self, pairs):
            return [-100.0 if "подбирают наставников" in text else 0.0 for _, text in pairs]

    kb._reranker = BadReranker() if has_reranker else None
    relevant = (
        "Руководители структурных подразделений подбирают наставников "
        "для конкретных наставляемых.",
        {},
        0.75,
    )
    candidates = [
        (f"Наставничество в крупной организации: общие положения {index}", {}, 0.99 - index / 100)
        for index in range(20)
    ]
    candidates.insert(5, relevant)

    query = "Кто подбирает наставника для конкретного наставляемого в крупной организации?"
    ranked = kb._rerank_candidates(query, candidates)

    assert relevant in ranked


class _FakeCollection:
    """Минимальная заглушка Chroma: отдаёт документы, содержащие подстроку."""

    def __init__(self, docs):
        self._docs = docs
        self.last_where = None

    def get(self, *, where, where_document, include, limit):
        self.last_where = where
        needle = where_document["$contains"]
        hits = [d for d in self._docs if needle in d][:limit]
        return {"documents": hits, "metadatas": [{"source_file": "f.pdf"} for _ in hits]}


def _kb_with(docs):
    from src.knowledge_base import KnowledgeBase as KB

    kb = object.__new__(KB)
    kb._col = _FakeCollection(docs)
    return kb


def test_pinned_point_reaches_the_context():
    kb = _kb_with(["6.2.13 Номинальный тепловой поток не следует принимать менее..."])
    ranked = kb._pin_point_matches(
        "Что требует пункт 6.2.13?", [], file_filter="f.pdf", workspace_id="ws-1"
    )
    assert ranked and "6.2.13" in ranked[0][0]


def test_pinned_product_identifier_reaches_the_context_case_insensitively():
    kb = _kb_with(["Перед заменой фильтра Q250 питание отключают на 17 минут."])
    ranked = kb._pin_point_matches(
        "Можно менять q250 через десять минут?", [],
        file_filter="f.pdf", workspace_id="ws-1",
    )
    assert ranked and "Q250" in ranked[0][0]


def test_exact_identifier_reduces_semantic_candidate_pool_without_losing_match(
    tmp_path, monkeypatch
):
    kb = make_kb(tmp_path)
    kb.add_book(
        write_doc(
            tmp_path,
            "manual.txt",
            "Перед заменой фильтра Q250 питание отключают на 17 минут. " * 5,
        ),
        workspace_id="workspace-a",
    )
    calls = []
    original = kb._raw_search

    def measured(queries, kw_filter=None, top_k=None):
        calls.append(top_k)
        return original(queries, kw_filter, top_k)

    monkeypatch.setattr(kb, "_raw_search", measured)

    _context, sources = kb.search_with_sources(
        "Можно менять q250 через десять минут?",
        file_filter="manual.txt",
        workspace_id="workspace-a",
    )

    assert calls == [config.RERANK_TOP_K]
    assert any("Q250" in source["text"] for source in sources)


def test_question_without_exact_address_keeps_full_semantic_candidate_pool(
    tmp_path, monkeypatch
):
    kb = make_kb(tmp_path)
    kb.add_book(
        write_doc(tmp_path, "manual.txt", "Правило безопасной замены фильтра. " * 5),
        workspace_id="workspace-a",
    )
    calls = []
    original = kb._raw_search

    def measured(queries, kw_filter=None, top_k=None):
        calls.append(top_k)
        return original(queries, kw_filter, top_k)

    monkeypatch.setattr(kb, "_raw_search", measured)
    kb.search_with_sources(
        "Как безопасно заменить фильтр?",
        file_filter="manual.txt",
        workspace_id="workspace-a",
    )

    assert calls == [None]


def test_pinned_lookup_ignores_the_section_filter_but_keeps_the_workspace():
    """Раздел не сужает адресный поиск, workspace - обязан сужать."""
    kb = _kb_with(["7.4.5 Требование"])
    kb._pin_point_matches(
        "по 7.4.5", [], file_filter="all", workspace_id="ws-1"
    )
    assert kb._col.last_where == {"workspace_id": "ws-1"}


def test_a_question_without_an_address_is_left_alone():
    kb = _kb_with(["что угодно"])
    ranked = [("исходный фрагмент", {}, 0.5)]
    assert kb._pin_point_matches(
        "Какая температура теплоносителя?", ranked, file_filter="all", workspace_id="ws-1"
    ) is ranked


# --- Фильтр по разделу не должен срабатывать по двум общим словам ----------
#
# Замер 04.09.2026 на СП 60.13330.2020: вопрос «какую скорость воды в
# трубопроводе можно допустить» выбирал раздел «ДОПУСТИМАЯ СКОРОСТЬ И
# ТЕМПЕРАТУРА В СТРУЕ ПРИТОЧНОГО ВОЗДУХА» - совпадали «допустимый» и
# «скорость», два самых общих слова из шести. Поиск сужался на приточные
# струи, нужная таблица в контекст не попадала, и человек получал «информация
# не найдена» по документу, где ответ есть.

_SP60_SECTIONS = [
    "ДОПУСТИМАЯ СКОРОСТЬ И ТЕМПЕРАТУРА В СТРУЕ ПРИТОЧНОГО ВОЗДУХА",
    "ДОПУСТИМАЯ СКОРОСТЬ ДВИЖЕНИЯ ТЕПЛОХЛАДОНОСИТЕЛЯ В ТРУБОПРОВОДАХ",
    "МЕТОДИКА РАСЧЕТА ВОЗДУХОРАСПРЕДЕЛЕНИЯ",
]


def _kb_with_sections(monkeypatch, tmp_path, sections):
    kb = make_kb(tmp_path)
    monkeypatch.setattr(
        KnowledgeBase, "get_available_sections", lambda self, *, workspace_id: sections
    )
    return kb


def test_two_generic_words_do_not_pick_the_wrong_section(monkeypatch, tmp_path):
    """Главное - не увести поиск в чужой раздел.

    Утверждаем именно это, а не «вернулся None»: если название подходящего
    раздела в вопросе действительно названо, выбрать его правильно. Ошибка,
    которую мы чиним, - выбор РАЗДЕЛА ПРО ДРУГОЕ по двум общим словам.
    """
    kb = _kb_with_sections(monkeypatch, tmp_path, _SP60_SECTIONS)
    q = ("Помещение с допустимым уровнем шума 35 дБА, сумма коэффициентов "
         "местных сопротивлений узла - до 15. Какую скорость воды в "
         "трубопроводе можно допустить?")
    assert kb.find_section_in_query(q, workspace_id="ws-any") != (
        "ДОПУСТИМАЯ СКОРОСТЬ И ТЕМПЕРАТУРА В СТРУЕ ПРИТОЧНОГО ВОЗДУХА"
    )


def test_truncated_section_title_does_not_capture_a_generic_question(monkeypatch, tmp_path):
    """Как в реальном документе: заголовок разорван переносом строки.

    На СП 60 раздел лежит в базе как «ТЕПЛОХЛАДОНОСИТЕЛЯ В ТРУБОПРОВОДАХ» -
    начало названия осталось на предыдущей строке. От такого огрызка одного
    совпавшего слова («трубопровод») быть достаточно не должно.
    """
    kb = _kb_with_sections(
        monkeypatch, tmp_path,
        ["ТЕПЛОХЛАДОНОСИТЕЛЯ В ТРУБОПРОВОДАХ", "МЕТОДИКА РАСЧЕТА ВОЗДУХОРАСПРЕДЕЛЕНИЯ"],
    )
    q = "Какую скорость воды в трубопроводе можно допустить при 35 дБА?"
    assert kb.find_section_in_query(q, workspace_id="ws-any") is None


def test_naming_a_section_still_picks_it(monkeypatch, tmp_path):
    kb = _kb_with_sections(monkeypatch, tmp_path, _SP60_SECTIONS)
    got = kb.find_section_in_query(
        "что сказано в методике расчета воздухораспределения?", workspace_id="ws-any"
    )
    assert got == "МЕТОДИКА РАСЧЕТА ВОЗДУХОРАСПРЕДЕЛЕНИЯ"


def test_best_matching_section_wins_not_the_first(monkeypatch, tmp_path):
    """Выбор не должен зависеть от порядка разделов в документе."""
    kb = _kb_with_sections(monkeypatch, tmp_path, _SP60_SECTIONS)
    got = kb.find_section_in_query(
        "допустимая скорость движения теплохладоносителя в трубопроводах",
        workspace_id="ws-any",
    )
    assert got == "ДОПУСТИМАЯ СКОРОСТЬ ДВИЖЕНИЯ ТЕПЛОХЛАДОНОСИТЕЛЯ В ТРУБОПРОВОДАХ"


# --- Общие слова документа не могут работать фильтром ---------------------
#
# Замер 04.09.2026 на СП 60, уже ПОСЛЕ введения порога доли слов. Вопрос про
# влагосодержание в Феодосии выбрал раздел «ВЕНТИЛЯЦИИ И КОНДИЦИОНИРОВАНИЯ
# ВОЗДУХА»: совпали «кондиционирование» и «воздух» - 2 слова из 3, порог
# пройден. Поиск сузился с 14085 символов до 7286, таблица с городами выпала.
#
# Порог тут бессилен: у обрывка заголовка из трёх общих слов любая доля будет
# высокой. Различать надо не сколько слов совпало, а какие.

_GENERIC_SECTIONS = [
    "ВЕНТИЛЯЦИИ И КОНДИЦИОНИРОВАНИЯ ВОЗДУХА",
    "СИСТЕМ ВЕНТИЛЯЦИИ И КОНДИЦИОНИРОВАНИЯ",
    "МЕТОДИКА РАСЧЕТА ВОЗДУХОРАСПРЕДЕЛЕНИЯ",
    "РАСЧЕТ ТЕПЛОВЫХ НАГРУЗОК НА СИСТЕМЫ ОТОПЛЕНИЯ",
]


def test_section_of_only_generic_words_is_never_chosen(monkeypatch, tmp_path):
    kb = _kb_with_sections(monkeypatch, tmp_path, _GENERIC_SECTIONS)
    q = ("Считаю кондиционирование для объекта в Феодосии. Какое "
         "влагосодержание наружного воздуха в тёплый период надо заложить?")
    got = kb.find_section_in_query(q, workspace_id="ws-any")
    assert got not in (
        "ВЕНТИЛЯЦИИ И КОНДИЦИОНИРОВАНИЯ ВОЗДУХА",
        "СИСТЕМ ВЕНТИЛЯЦИИ И КОНДИЦИОНИРОВАНИЯ",
    )


def test_section_with_its_own_words_still_matches(monkeypatch, tmp_path):
    """«Воздухораспределение» есть только в одном заголовке - это примета."""
    kb = _kb_with_sections(monkeypatch, tmp_path, _GENERIC_SECTIONS)
    got = kb.find_section_in_query(
        "что сказано в методике расчета воздухораспределения?", workspace_id="ws-any"
    )
    assert got == "МЕТОДИКА РАСЧЕТА ВОЗДУХОРАСПРЕДЕЛЕНИЯ"


def test_question_about_the_whole_document_picks_nothing(monkeypatch, tmp_path):
    kb = _kb_with_sections(monkeypatch, tmp_path, _GENERIC_SECTIONS)
    assert kb.find_section_in_query(
        "какие требования к системам вентиляции и кондиционирования?",
        workspace_id="ws-any",
    ) is None


def test_generic_working_time_question_does_not_select_schedule_chapter(
    monkeypatch, tmp_path
):
    """A topic is not automatically the name of a narrower chapter.

    The real Labour Code eval asks for the normal weekly working time.  With a
    60% morphology threshold, the words ``рабочего времени`` incorrectly
    selected ``РЕЖИМ РАБОЧЕГО ВРЕМЕНИ`` and discarded article 91 from the
    neighbouring ``ОБЩИЕ ПОЛОЖЕНИЯ`` chapter before reranking.
    """
    kb = _kb_with_sections(
        monkeypatch,
        tmp_path,
        [
            "Глава 15. ОБЩИЕ ПОЛОЖЕНИЯ",
            "Глава 16. РЕЖИМ РАБОЧЕГО ВРЕМЕНИ",
            "Глава 17. ВРЕМЯ ОТДЫХА",
        ],
    )

    assert kb.find_section_in_query(
        "Какая нормальная продолжительность рабочего времени в неделю?",
        workspace_id="ws-any",
    ) is None


def test_inflected_explicit_section_name_still_selects_schedule_chapter(
    monkeypatch, tmp_path
):
    kb = _kb_with_sections(
        monkeypatch,
        tmp_path,
        [
            "Глава 15. ОБЩИЕ ПОЛОЖЕНИЯ",
            "Глава 16. РЕЖИМ РАБОЧЕГО ВРЕМЕНИ",
            "Глава 17. ВРЕМЯ ОТДЫХА",
        ],
    )

    assert kb.find_section_in_query(
        "Что сказано в главе о режиме рабочего времени?",
        workspace_id="ws-any",
    ) == "Глава 16. РЕЖИМ РАБОЧЕГО ВРЕМЕНИ"
