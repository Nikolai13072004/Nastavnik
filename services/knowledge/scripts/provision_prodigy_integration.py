"""Provision or rotate one Prodigy service credential in the Vedomo database.

Run only on the intended Vedomo instance. The raw token is printed once and
never stored; transfer it through a secret manager, not chat or Git.
"""

import argparse
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.db import SessionLocal
from src.db_models import Document, ProdigyIntegration, Workspace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization-id", required=True)
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--expires-days", type=int, default=30)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--rotate", action="store_true")
    operation.add_argument("--revoke", action="store_true")
    parser.add_argument("--rebind-from-course", help="Expected previous course; requires --rotate")
    args = parser.parse_args()
    if (not 1 <= len(args.organization_id) <= 128
            or not 1 <= len(args.course_id) <= 128
            or not 1 <= args.expires_days <= 365):
        parser.error("organization ID, course ID or expiry out of range")
    if args.rebind_from_course is not None and (
        not args.rotate or not 1 <= len(args.rebind_from_course) <= 128
        or args.rebind_from_course == args.course_id
    ):
        parser.error("course rebinding requires --rotate and a different expected previous course")

    token = None
    with SessionLocal.begin() as db:
        workspace = db.get(Workspace, args.workspace_id)
        if workspace is None or workspace.kind != "course" or workspace.is_archived:
            parser.error("workspace must be an active dedicated course workspace")
        credential = db.scalar(select(ProdigyIntegration).where(
            ProdigyIntegration.organization_id == args.organization_id
        ))
        other = db.scalar(select(ProdigyIntegration).where(
            ProdigyIntegration.workspace_id == args.workspace_id
        ))
        expected_course = args.rebind_from_course or args.course_id
        if credential and (not (args.rotate or args.revoke) or credential.workspace_id != args.workspace_id
                           or credential.course_id != expected_course):
            parser.error("organization already provisioned; use --rotate with its existing course and workspace")
        if other and other is not credential:
            parser.error("workspace already belongs to another organization")
        if args.rotate and credential is None:
            parser.error("nothing to rotate")
        if args.revoke and credential is None:
            parser.error("nothing to revoke")
        if args.revoke:
            credential.is_active = False
        else:
            if credential is None and db.scalar(select(Document.id).where(
                Document.workspace_id == args.workspace_id
            )) is not None:
                parser.error("new integration workspace must be empty before provisioning")

            token = secrets.token_urlsafe(48)
            if credential is None:
                credential = ProdigyIntegration(
                    organization_id=args.organization_id,
                    course_id=args.course_id,
                    workspace_id=args.workspace_id,
                    token_hash="",
                    expires_at=datetime.now(timezone.utc),
                )
                db.add(credential)
            credential.token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
            credential.course_id = args.course_id
            credential.expires_at = datetime.now(timezone.utc) + timedelta(days=args.expires_days)
            credential.is_active = True

    print("Prodigy integration revoked" if token is None else "PRODIGY_SERVICE_TOKEN=" + token)


if __name__ == "__main__":
    main()
