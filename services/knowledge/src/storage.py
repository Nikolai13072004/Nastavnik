"""Workspace-scoped file storage helpers.

Authenticated uploads (Stage 3c+) live under
``docs/<workspace_id>/<document_id>__<safe_filename>`` — the ``Document``
record in SQL is the source of truth, ``document_id`` makes the path
collision-free across re-uploads with the same original name, and the
``__`` separator keeps the original name human-readable in directory
listings.

The legacy direct/anonymous path has no ``Document`` table and keeps the
Stage 2 layout ``docs/<workspace_id>/<safe_filename>`` via
``legacy_workspace_file_path``.
"""

from __future__ import annotations

import glob
import os
import re

import config


# Characters that are not safe to use verbatim in a filename on disk
# (path separators, drive letters, NUL, etc.).
_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

_MAX_NAME_LENGTH = 200


def sanitize_filename(name: str) -> str:
    """Return a filesystem-safe version of ``name``.

    Strips directory components, removes ``..`` traversal sequences and any
    characters that are unsafe on common filesystems, and caps the length.
    Falls back to ``"file"`` if nothing usable remains.
    """
    candidate = os.path.basename(str(name or "").strip())
    candidate = candidate.replace("..", "")
    candidate = _UNSAFE_CHARS.sub("_", candidate)
    candidate = candidate.strip(" .")

    if not candidate:
        return "file"

    if len(candidate) > _MAX_NAME_LENGTH:
        root, ext = os.path.splitext(candidate)
        candidate = root[: max(1, _MAX_NAME_LENGTH - len(ext))] + ext

    return candidate


def workspace_docs_dir(workspace_id: str) -> str:
    """Return ``docs/<workspace_id>/`` (sanitizing ``workspace_id`` itself)."""
    safe_workspace_id = sanitize_filename(workspace_id)
    return os.path.join(config.DOCS_DIR, safe_workspace_id)


def document_stored_path(workspace_id: str, document_id: str, original_name: str) -> str:
    """Return the on-disk path for a Document.

    Layout: ``docs/<workspace_id>/<document_id>__<safe_filename>``. Two uploads
    with the same ``original_name`` in the same workspace land on different
    paths because ``document_id`` is a UUID; the ``Document`` table enforces
    that there is at most one *active* document per ``(workspace_id, original_name)``
    via the application-level replace flow.
    """
    safe_document_id = sanitize_filename(document_id)
    safe_name = sanitize_filename(original_name)
    return os.path.join(workspace_docs_dir(workspace_id), f"{safe_document_id}__{safe_name}")


def resolve_stored_path(stored_path: str, workspace_id: str) -> str:
    """Найти файл материала, даже если ``Document.stored_path`` устарел.

    ``stored_path`` абсолютный (``DOCS_DIR`` считается от корня проекта), поэтому
    он «протухает» вместе с местом установки: перенесли папку проекта — и все
    записи указывают в никуда; то же самое при переносе БД между Docker
    (``/app/docs``) и локальной машиной. Сами файлы при этом на месте, и путь к
    ним восстановим: имя на диске содержит ``document_id``, а раскладка
    ``docs/<workspace_id>/`` не менялась.

    Возвращает существующий путь, если он нашёлся; иначе — исходный, чтобы
    вызывающий код сообщил об отсутствии файла как раньше, а не молча подставил
    несуществующее имя.
    """
    if not stored_path:
        return stored_path
    if os.path.isfile(stored_path):
        return stored_path

    # Имя файла отделяем сами, а не через os.path.basename: он знает только
    # разделитель текущей ОС, поэтому на Linux вернул бы windows-путь целиком —
    # и главный сценарий (база с Windows развёрнута в Docker) не сработал бы.
    file_name = re.split(r"[\\/]", stored_path)[-1]
    if not file_name:
        return stored_path

    candidate = os.path.join(workspace_docs_dir(workspace_id), file_name)
    return candidate if os.path.isfile(candidate) else stored_path


def legacy_workspace_file_path(workspace_id: str, original_name: str) -> str:
    """Stage 2 layout: ``docs/<workspace_id>/<safe_filename>``.

    Used only by the legacy direct/anonymous path, which does not create
    ``Document`` rows. Authenticated API code MUST use
    :func:`document_stored_path` instead so paths and the SQL table agree.
    """
    return os.path.join(workspace_docs_dir(workspace_id), sanitize_filename(original_name))


def is_workspace_library_path(file_path: str, workspace_id: str) -> bool:
    """True if ``file_path`` is a top-level file inside ``docs/<workspace_id>/``.

    Mirrors the previous single-tenant check (``KnowledgeBase._is_library_file_path``)
    but scopes it to one workspace's directory, and rejects files in nested
    subdirectories of that workspace.
    """
    if not file_path:
        return False

    base_dir = os.path.normcase(os.path.abspath(workspace_docs_dir(workspace_id)))
    normalized_path = os.path.normcase(os.path.abspath(file_path))

    try:
        common = os.path.commonpath([base_dir, normalized_path])
    except ValueError:
        return False

    if common != base_dir:
        return False

    relative = os.path.relpath(normalized_path, base_dir)
    if relative.startswith(".."):
        return False

    return os.path.dirname(relative) in ("", ".")


def iter_workspace_library_files(workspace_id: str):
    """Return sorted absolute paths of supported files in ``docs/<workspace_id>/``."""
    docs_dir = workspace_docs_dir(workspace_id)
    os.makedirs(docs_dir, exist_ok=True)

    files = []
    for ext in config.SUPPORTED_FORMATS:
        files.extend(glob.glob(os.path.join(docs_dir, f"*{ext}")))

    return sorted(
        {
            os.path.abspath(file_path)
            for file_path in files
            if is_workspace_library_path(file_path, workspace_id)
        }
    )
