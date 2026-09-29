"""Load an approved demo document into an isolated Prodigy course workspace."""

import argparse
import hashlib
import secrets
from pathlib import Path

from sqlalchemy import select

from src.db import SessionLocal
from src.db_models import Document, User, Workspace, WorkspaceMember
from src.document_service import create_document
from src.security import hash_password


OWNER_EMAIL = "max-pilot-content@vedomo.invalid"
WORKSPACE_NAME = "Prodigy MAX - первый день"
DOCUMENT_NAME = "onboarding-policy.md"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("document", type=Path)
    args = parser.parse_args()
    content = args.document.read_bytes()
    content_hash = hashlib.sha256(content).hexdigest()

    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == OWNER_EMAIL))
        if owner is None:
            owner = User(
                email=OWNER_EMAIL,
                password_hash=hash_password(secrets.token_urlsafe(48)),
                display_name="Prodigy MAX content import",
                is_active=False,
            )
            db.add(owner)
            db.flush()

        workspace = db.scalar(select(Workspace).where(
            Workspace.owner_user_id == owner.id,
            Workspace.name == WORKSPACE_NAME,
        ))
        if workspace is None:
            workspace = Workspace(
                name=WORKSPACE_NAME,
                owner_user_id=owner.id,
                kind="course",
                join_enabled=False,
            )
            db.add(workspace)
            db.flush()
            db.add(WorkspaceMember(
                workspace_id=workspace.id,
                user_id=owner.id,
                role="owner",
            ))
        if workspace.kind != "course" or workspace.is_archived:
            parser.error("pilot workspace is not an active course")
        db.commit()

        document = db.scalar(select(Document).where(
            Document.workspace_id == workspace.id,
            Document.original_name == DOCUMENT_NAME,
        ))
        if document is None:
            document = create_document(
                db,
                workspace_id=workspace.id,
                owner_user_id=owner.id,
                original_name=DOCUMENT_NAME,
                content=content,
            )
        if document.status != "ready" or document.content_hash != content_hash:
            parser.error("pilot document is not ready or its hash differs")

        print(f"workspace_id={workspace.id}")
        print(f"document_id={document.id}")
        print(f"document_hash={document.content_hash}")


if __name__ == "__main__":
    main()
