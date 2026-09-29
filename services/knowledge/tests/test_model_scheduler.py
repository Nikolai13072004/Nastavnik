from threading import Event, Thread
import time

from src.model_scheduler import ModelScheduler


def test_waiting_chat_runs_before_next_index_batch():
    scheduler = ModelScheduler(max_parallel_chats=1)
    first_index_started = Event()
    release_first_index = Event()
    order = []

    def first_index():
        with scheduler.indexing():
            order.append("index-1")
            first_index_started.set()
            release_first_index.wait(2)

    def chat():
        first_index_started.wait(2)
        with scheduler.chat():
            order.append("chat")

    def second_index():
        first_index_started.wait(2)
        with scheduler.indexing():
            order.append("index-2")

    threads = [Thread(target=first_index), Thread(target=chat), Thread(target=second_index)]
    for thread in threads:
        thread.start()
    first_index_started.wait(2)
    # Give both waiters a chance to register before releasing the active batch.
    time.sleep(0.03)
    release_first_index.set()
    for thread in threads:
        thread.join(2)

    assert order == ["index-1", "chat", "index-2"]
    assert all(not thread.is_alive() for thread in threads)
