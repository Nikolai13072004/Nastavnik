from src.knowledge_base import KnowledgeBase
from src import storage
import config


def test_section_sort_key_orders_chapters_naturally():
    sections = ["Глава 12", "Глава 2", "Глава 1", "Глава 10"]

    assert sorted(sections, key=KnowledgeBase._section_sort_key) == [
        "Глава 1",
        "Глава 2",
        "Глава 10",
        "Глава 12",
    ]


def test_section_sort_key_keeps_paragraph_numbers_natural():
    sections = ["§ 10. Экономика", "§ 2. Начало", "§ 11. Политика"]

    assert sorted(sections, key=KnowledgeBase._section_sort_key) == [
        "§ 2. Начало",
        "§ 10. Экономика",
        "§ 11. Политика",
    ]


def test_is_library_file_path_accepts_workspace_docs_file(tmp_path, monkeypatch):
    docs_dir = tmp_path / "docs"
    workspace_dir = docs_dir / "workspace-a"
    workspace_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "DOCS_DIR", str(docs_dir))

    target = workspace_dir / "book.pdf"

    assert storage.is_workspace_library_path(str(target), "workspace-a") is True


def test_is_library_file_path_rejects_internal_subdirectory_file(tmp_path, monkeypatch):
    docs_dir = tmp_path / "docs"
    (docs_dir / "workspace-a" / "specs").mkdir(parents=True)
    monkeypatch.setattr(config, "DOCS_DIR", str(docs_dir))

    target = docs_dir / "workspace-a" / "specs" / "design.md"

    assert storage.is_workspace_library_path(str(target), "workspace-a") is False

def test_is_library_file_path_rejects_other_workspace_file(tmp_path, monkeypatch):
    docs_dir = tmp_path / "docs"
    (docs_dir / "workspace-a").mkdir(parents=True)
    (docs_dir / "workspace-b").mkdir(parents=True)
    monkeypatch.setattr(config, "DOCS_DIR", str(docs_dir))

    target = docs_dir / "workspace-b" / "book.pdf"

    assert storage.is_workspace_library_path(str(target), "workspace-a") is False
