"""Check the real local Prodigy API with a synthetic document, never user data."""

import argparse
import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import requests
from sqlalchemy import select, update

import config
from src.db import SessionLocal
from src.db_models import Document, ProdigyIntegration


TEXT = (
    "Проверочный регламент для изолированного локального теста. "
    "Изменение регламента утверждает начальник отдела качества. "
    "Сотрудник знакомится с новой редакцией до выполнения проверки знаний. "
    "Ответственный сотрудник фиксирует дату утверждения и сообщает команде. "
) * 4
QUESTION = "Кто утверждает изменение регламента?"


def run_smoke() -> None:
    from src import app_services, auth_service
    from src.auth_models import UserCreate

    run_id = uuid4().hex
    token = secrets.token_urlsafe(48)
    password = secrets.token_urlsafe(24)
    email = f"local-smoke-{run_id}@example.com"
    organization_id = f"smoke-org-{run_id}"
    course_id = f"smoke-course-{run_id}"

    with SessionLocal() as db:
        owner = auth_service.register_user(db, UserCreate(
            email=email, password=password, display_name="Local integration smoke",
        ))
        owner.can_create_courses = True
        db.commit()
        workspace = app_services.create_course_service(db, owner, "Local integration smoke")
        db.add(ProdigyIntegration(
            organization_id=organization_id,
            course_id=course_id,
            workspace_id=workspace.id,
            token_hash=hashlib.sha256(token.encode("ascii")).hexdigest(),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ))
        db.commit()
        workspace_id = workspace.id

    base_url = "http://127.0.0.1:8000/api"
    browser = requests.Session()
    login = browser.post(
        f"{base_url}/auth/login",
        json={"email": email, "password": password},
        timeout=15,
    )
    login.raise_for_status()
    upload = browser.post(
        f"{base_url}/materials/upload",
        headers={"X-Workspace-ID": workspace_id},
        files={"file": ("local-integration-smoke.txt", TEXT.encode("utf-8"), "text/plain")},
        timeout=30,
    )
    upload.raise_for_status()
    if not upload.json()["ok"]:
        raise RuntimeError(f"Synthetic document upload failed: {upload.json()['message']}")

    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        progress = browser.get(
            f"{base_url}/materials/progress",
            headers={"X-Workspace-ID": workspace_id},
            timeout=15,
        )
        progress.raise_for_status()
        if not progress.json()["active"]:
            break
        time.sleep(2)
    else:
        raise RuntimeError("Synthetic document indexing timed out")

    with SessionLocal() as db:
        document = db.query(Document).filter_by(workspace_id=workspace_id).one()
        if document.status != "ready":
            raise RuntimeError("Synthetic document is not ready")
        document_id = document.id

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Prodigy-Organization-ID": organization_id,
        "X-Prodigy-Course-ID": course_id,
    }
    integration_url = f"{base_url}/integrations/prodigy"
    lookup = requests.get(f"{integration_url}/documents/{document_id}", headers=headers, timeout=15)
    lookup.raise_for_status()
    if lookup.json()["document_hash"] != hashlib.sha256(TEXT.encode("utf-8")).hexdigest():
        raise RuntimeError("Document hash differs from the uploaded bytes")
    wrong_course = requests.get(
        f"{integration_url}/documents/{document_id}",
        headers={**headers, "X-Prodigy-Course-ID": "another-course"},
        timeout=15,
    )
    if wrong_course.status_code != 401:
        raise RuntimeError("A foreign course could access the integration")
    wrong_organization = requests.get(
        f"{integration_url}/documents/{document_id}",
        headers={**headers, "X-Prodigy-Organization-ID": "another-organization"},
        timeout=15,
    )
    if wrong_organization.status_code != 401:
        raise RuntimeError("A foreign organization could access the integration")

    response = requests.post(
        f"{integration_url}/chat", headers=headers, json={"question": QUESTION}, timeout=180
    )
    response.raise_for_status()
    answer = response.json()
    if answer["refused"] or not answer["sources"]:
        raise RuntimeError("Local answer was refused or has no verified source")
    if any(source["document_id"] != document_id for source in answer["sources"]):
        raise RuntimeError("Answer cited a foreign document")

    with SessionLocal.begin() as db:
        db.get(Document, document_id).status = "hidden"
    hidden = requests.get(f"{integration_url}/documents/{document_id}", headers=headers, timeout=15)
    if hidden.status_code != 404:
        raise RuntimeError("A hidden document remained visible")
    hidden_answer = requests.post(
        f"{integration_url}/chat", headers=headers, json={"question": QUESTION}, timeout=180
    )
    hidden_answer.raise_for_status()
    if not hidden_answer.json()["refused"] or hidden_answer.json()["sources"]:
        raise RuntimeError("A hidden document still supported an answer")

    with SessionLocal.begin() as db:
        db.query(ProdigyIntegration).filter_by(organization_id=organization_id).one().is_active = False
    revoked = requests.get(f"{integration_url}/documents/{document_id}", headers=headers, timeout=15)
    if revoked.status_code != 401:
        raise RuntimeError("A revoked integration key remained active")
    print(f"Local integration passed: {len(answer['sources'])} verified source(s)")


def hide_previous_smoke_runs() -> None:
    with SessionLocal.begin() as db:
        credentials = db.scalars(select(ProdigyIntegration).where(
            ProdigyIntegration.organization_id.like("smoke-org-%")
        )).all()
        workspace_ids = [credential.workspace_id for credential in credentials]
        for credential in credentials:
            credential.is_active = False
        if workspace_ids:
            db.execute(update(Document).where(
                Document.workspace_id.in_(workspace_ids),
                Document.original_name == "local-integration-smoke.txt",
            ).values(status="hidden"))


def main() -> None:
    if config.DATABASE_URL != "sqlite:////app/data/max-local.db":
        raise RuntimeError("This smoke check is restricted to the isolated local database")
    parser = argparse.ArgumentParser()
    parser.parse_args()
    hide_previous_smoke_runs()
    try:
        run_smoke()
    finally:
        hide_previous_smoke_runs()


if __name__ == "__main__":
    main()
