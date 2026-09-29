"""InMemoryProgressStore unit tests (Phase A-prep, Stage 56). Pure, CI-safe -
locks the seam's contract so a future shared-backend store has a spec to meet."""

from src.progress_store import IDLE_PROGRESS_STATE, InMemoryProgressStore


def test_get_returns_idle_copy_when_unknown():
    store = InMemoryProgressStore()
    snap = store.get("ws")
    assert snap == IDLE_PROGRESS_STATE
    # The result is a copy: mutating it must not leak back into the store.
    snap["progress"] = 99
    assert store.get("ws")["progress"] == 0


def test_set_merges_fields_onto_idle_defaults():
    store = InMemoryProgressStore()
    store.set("ws", active=True, progress=40, phase="indexing")
    snap = store.get("ws")
    assert snap["active"] is True
    assert snap["progress"] == 40
    assert snap["phase"] == "indexing"
    assert snap["operation"] == "idle"  # unset fields keep idle defaults


def test_cancel_flag_lifecycle():
    store = InMemoryProgressStore()
    assert store.is_cancel_requested("ws") is False
    store.request_cancel("ws")
    assert store.is_cancel_requested("ws") is True
    store.clear_cancel("ws")
    assert store.is_cancel_requested("ws") is False


def test_reset_clears_progress_and_cancel():
    store = InMemoryProgressStore()
    store.set("ws", active=True)
    store.request_cancel("ws")
    store.reset()
    assert store.get("ws") == IDLE_PROGRESS_STATE
    assert store.is_cancel_requested("ws") is False


def test_workspaces_are_isolated():
    store = InMemoryProgressStore()
    store.set("a", progress=10)
    store.request_cancel("a")
    assert store.get("b")["progress"] == 0
    assert store.is_cancel_requested("b") is False


# --- Снимок прогресса обязан называть своё пространство --------------------
#
# Опрос прогресса не может спросить «как там мой файл»: он спрашивает «что
# происходит в текущем пространстве», а оно меняется под ним, если человек
# переключит курс посреди загрузки. Без штампа фронт принимал состояние
# ЧУЖОГО пространства за своё - пустое трактовал как «работа ещё не началась»
# и ждал вечно, а чужое «готово» засчитывал как успех своего файла.
#
# Сама загрузка при этом доводится до конца верно: workspace_id захвачен в
# замыкание фонового потока. Ломался только показ.


def test_progress_snapshot_names_its_workspace():
    from src.app_services import get_material_progress

    assert get_material_progress("ws-a").workspace_id == "ws-a"


def test_empty_snapshot_also_names_its_workspace():
    """Пустой снимок опаснее всего: именно он выглядел как «ещё не началось»."""
    from src.app_services import get_material_progress, reset_material_progress_for_tests

    reset_material_progress_for_tests()
    snap = get_material_progress("ws-never-touched")
    assert snap.active is False
    assert snap.workspace_id == "ws-never-touched"


def test_two_workspaces_do_not_share_a_stamp():
    from src import app_services

    app_services.reset_material_progress_for_tests()
    app_services._start_material_progress("ws-a", operation="upload", message="идёт")
    a = app_services.get_material_progress("ws-a")
    b = app_services.get_material_progress("ws-b")

    assert a.workspace_id == "ws-a" and a.active is True
    # У второго пространства своя (пустая) история, и снимок это признаёт.
    assert b.workspace_id == "ws-b" and b.active is False
