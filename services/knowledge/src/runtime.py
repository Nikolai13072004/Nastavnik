"""Shared runtime objects for Vedomo interfaces."""

from threading import RLock

from src.knowledge_base import KnowledgeBase
from src.llm_engine import LLMEngine


_llm = None
_kb = None
_lock = RLock()


def get_llm():
    """Return the shared LLM engine instance."""
    global _llm
    with _lock:
        if _llm is None:
            _llm = LLMEngine()
        return _llm


def get_kb(log=None):
    """Return the shared knowledge base instance."""
    global _kb
    with _lock:
        if _kb is None:
            _kb = KnowledgeBase(progress_callback=log, llm_engine=get_llm())
        elif getattr(_kb, "_llm", None) is None:
            _kb.set_llm(get_llm())
        return _kb


def reset_runtime_for_tests():
    """Reset shared runtime objects in tests."""
    global _llm, _kb
    with _lock:
        _llm = None
        _kb = None
