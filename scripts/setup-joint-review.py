"""Index the approved source in an empty, local-only Vedomo review database."""

import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import func, select

from src import runtime
from src.db import SessionLocal
from src.db_models import Document, ProdigyIntegration, User, Workspace, WorkspaceMember
from src.document_service import create_document
from src.security import hash_password


WORKSPACE_ID = "max-joint-review-workspace"
OWNER_EMAIL = "joint-review@vedomo.invalid"
SOURCE_NAME = "onboarding-policy.md"
APPROVED_HASH = "4ae1eaa63cc6a90372bde88a52d1c94c573c14ef701f7fac262cfc52199acdd2"


def main():
    database = urlparse(os.environ.get("DATABASE_URL", ""))
    token = os.environ.get("REVIEW_SERVICE_TOKEN", "")
    if (os.environ.get("MAX_JOINT_REVIEW") != "true"
            or database.hostname != "vedomo-db"
            or database.path != "/max_joint_review"
            or not re.fullmatch(r"[A-Za-z0-9_-]{64}", token)):
        raise SystemExit("Only the isolated local joint-review database is allowed.")
    content = Path("/run/onboarding-policy.md").read_bytes()
    if hashlib.sha256(content).hexdigest() != APPROVED_HASH:
        raise SystemExit("The owner-approved source has changed; stop for review.")

    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == OWNER_EMAIL))
        if db.scalar(select(func.count()).select_from(User)) != (1 if owner else 0):
            raise SystemExit("Review database contains unrelated users.")
        workspace = db.get(Workspace, WORKSPACE_ID)
        if db.scalar(select(func.count()).select_from(Workspace)) != (1 if workspace else 0):
            raise SystemExit("Review database contains unrelated workspaces.")
        if owner is None:
            owner = User(email=OWNER_EMAIL, password_hash=hash_password(secrets.token_urlsafe(48)),
                         display_name="Local review content", is_active=False)
            db.add(owner)
            db.flush()
        if workspace is None:
            workspace = Workspace(id=WORKSPACE_ID, name="Prodigy local review", kind="course",
                                  owner_user_id=owner.id, join_enabled=False)
            db.add(workspace)
            db.flush()
            db.add(WorkspaceMember(workspace_id=workspace.id, user_id=owner.id, role="owner"))
        if workspace.owner_user_id != owner.id or workspace.kind != "course" or workspace.is_archived:
            raise SystemExit("Review workspace is not available.")
        credential = db.scalar(select(ProdigyIntegration).where(
            ProdigyIntegration.organization_id == "max-pilot-demo-org"))
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        if db.scalar(select(func.count()).select_from(ProdigyIntegration)) != (1 if credential else 0):
            raise SystemExit("Review database contains unrelated integrations.")
        if credential is None:
            credential = ProdigyIntegration(
                organization_id="max-pilot-demo-org", course_id="max-pilot-onboarding-course",
                workspace_id=workspace.id, token_hash=digest, is_active=True,
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
            db.add(credential)
        if (credential.workspace_id != workspace.id or credential.token_hash != digest
                or credential.course_id != "max-pilot-onboarding-course" or not credential.is_active):
            raise SystemExit("Review integration differs; do not replace it automatically.")
        document = db.scalar(select(Document).where(Document.workspace_id == workspace.id,
                                                     Document.original_name == SOURCE_NAME))
        if db.scalar(select(func.count()).select_from(Document)) != (1 if document else 0):
            raise SystemExit("Review database contains unrelated documents.")
        db.commit()
        if document is None:
            document = create_document(db, workspace_id=workspace.id, owner_user_id=owner.id,
                                       original_name=SOURCE_NAME, content=content)
        if document.status != "ready" or document.content_hash != APPROVED_HASH:
            raise SystemExit("Review source is not indexed or its hash differs.")
        chunks = runtime.get_kb().get_document_chunks(document.id, workspace_id=workspace.id)
        if not chunks:
            raise SystemExit("Review source has no indexed chunks.")
        print(json.dumps({"document_id": document.id, "document_hash": document.content_hash,
                          "indexed_chunks": len(chunks)}))


if __name__ == "__main__":
    main()
