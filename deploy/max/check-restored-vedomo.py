"""Check restored files and vector search; rotate only the isolated credential."""

import hashlib
import os
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select

import config
from src.db import SessionLocal
from src.db_models import Document, ProdigyIntegration
from src.knowledge_base import KnowledgeBase


def main():
    database = urlparse(config.DATABASE_URL)
    assert os.environ.get("MAX_JOINT_REVIEW") == "true"
    assert database.hostname == "vedomo-db" and database.path == "/max_joint_review"
    assert config.LLM_MODE == "ollama"
    assert os.environ.get("OLLAMA_MODEL") == "review-generation-disabled"
    token = os.environ["REVIEW_SERVICE_TOKEN"]
    print("RESTORE_CHECK: isolated environment verified.", flush=True)
    with SessionLocal() as session:
        credential = session.scalar(select(ProdigyIntegration).where(
            ProdigyIntegration.organization_id == "max-pilot-demo-org",
            ProdigyIntegration.course_id == "max-pilot-onboarding-course",
        ))
        assert credential is not None and credential.is_active
        workspace_id = credential.workspace_id
        documents = session.scalars(select(Document).where(Document.status == "ready")).all()
        assert documents
        for document in documents:
            path = Path(document.stored_path).resolve()
            assert path.is_relative_to(Path(config.DOCS_DIR).resolve())
            assert path.is_file() and path.stat().st_size == document.size_bytes
            assert hashlib.sha256(path.read_bytes()).hexdigest() == document.content_hash
        # No production token is copied into this review. The live database is untouched.
        credential.token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        session.commit()
        scoped = [document for document in documents if document.workspace_id == workspace_id]
        approved_hash = "4ae1eaa63cc6a90372bde88a52d1c94c573c14ef701f7fac262cfc52199acdd2"
        onboarding = next(document for document in scoped if document.content_hash == approved_hash)
        document_id = onboarding.id
        source_name = onboarding.original_name

    print("RESTORE_CHECK: stored files and database scope verified.", flush=True)
    kb = KnowledgeBase(progress_callback=lambda _message: None)
    assert kb._col.count() > 0, "The restored index must not be recreated empty"
    for document in documents:
        chunks = kb.get_document_chunks(document.id, workspace_id=document.workspace_id)
        assert chunks, "Each ready document must retain its indexed chunks"
    context, sources = kb.search_with_sources(
        "Как сотрудник получает доступ к обучению?", [document_id], workspace_id=workspace_id,
    )
    assert context and sources
    assert all(source["source_file"] == source_name for source in sources)
    print(f"RESTORED_VEDOMO: {len(documents)} files, retained chunks and scoped BGE-M3 search verified.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"RESTORED_VEDOMO_FAILED ({type(error).__name__}); private data not printed.") from None
