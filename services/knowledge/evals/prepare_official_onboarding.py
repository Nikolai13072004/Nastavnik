"""Build small, source-faithful materials for the onboarding evaluation.

The original Ministry of Labour files are kept in ``evals/sources``. Their
hashes pin the exact editions used to prepare the questions.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from bs4 import BeautifulSoup
from docx import Document


ROOT = Path(__file__).parent
SOURCES = ROOT / "sources" / "official-onboarding"
MATERIALS = ROOT / "datasets" / "materials" / "official-onboarding"
MENTORING_FILE = SOURCES / "mintrud-mentoring-2026.docx"
EMPLOYMENT_FILE = SOURCES / "mintrud-employment-2025.html"
SOURCE_HASHES = {
    MENTORING_FILE: "cabc9e9a7d7b70e9bd218f223cb14c864bd53c72aaa253ff32d87426b6559b80",
    EMPLOYMENT_FILE: "253de45852ee4666ae0185d1e9abdeba372c9fc593cb839e5dae9af3a16fcd4e",
}
MENTORING_URL = (
    "https://mintrud.gov.ru/uploads/editor/76/e3/"
    "%D0%A0%D0%B5%D0%BA%D0%BE%D0%BC%D0%B5%D0%BD%D0%B4%D0%B0%D1%86%D0%B8%D0%B8"
    "%20%D0%BF%D0%BE%20%D0%BD%D0%B0%D1%81%D1%82%D0%B0%D0%B2%D0%BD%D0%B8%D1%87"
    "%D0%B5%D1%81%D1%82%D0%B2%D1%83.docx"
)
EMPLOYMENT_URL = "https://mintrud.gov.ru/ministry/programms/trudotn/dogovor"


def verify_sources() -> None:
    for source, expected_hash in SOURCE_HASHES.items():
        content = source.read_bytes()
        if source == EMPLOYMENT_FILE:
            # Git may check out this text file with CRLF on Windows.
            content = content.replace(b"\r\n", b"\n")
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash != expected_hash:
            raise ValueError(f"Source edition changed: {source.name}")


def mentoring_material(
    paragraphs: list[str], start: int, end: int, section: str
) -> str:
    body = "\n\n".join(text for text in paragraphs[start:end] if text.strip())
    return (
        "Минтруд России. Рекомендации по организации наставничества в сфере труда. "
        "Утверждены 22 апреля 2026 г., протокол № 4пр.\n"
        f"Раздел: {section}.\n"
        f"Оригинал: {MENTORING_URL}\n"
        "Извлечение из официального документа; нормативную актуальность "
        "следует проверять отдельно.\n\n"
        f"{body}\n"
    )


def employment_material() -> str:
    soup = BeautifulSoup(EMPLOYMENT_FILE.read_text(encoding="utf-8"), "html.parser")
    heading = soup.find("h1", class_="page-title")
    if (
        heading is None
        or heading.get_text(" ", strip=True) != "Заключение трудового договора"
    ):
        raise ValueError("The employment page heading changed")
    article = heading.parent.find("div", class_="post-content")
    if article is None:
        raise ValueError("The employment page content is missing")

    lines: list[str] = []
    for block in article.find_all(["p", "ul"], recursive=False):
        if block.name == "p":
            lines.append(block.get_text(" ", strip=True))
        else:
            lines.extend(
                f"- {item.get_text(' ', strip=True)}" for item in block.find_all("li")
            )

    return (
        "Минтруд России. Заключение трудового договора. "
        "Страница изменена 7 марта 2025 г.\n"
        f"Оригинал: {EMPLOYMENT_URL}\n"
        "Извлечение из официальной страницы; нормативную актуальность "
        "следует проверять отдельно.\n\n" + "\n\n".join(lines) + "\n"
    )


def build_materials() -> dict[Path, str]:
    document = Document(MENTORING_FILE)
    paragraphs = [paragraph.text for paragraph in document.paragraphs]
    if paragraphs[44].strip() != "ОПРЕДЕЛЕНИЕ НАСТАВНИЧЕСТВА В СФЕРЕ ТРУДА":
        raise ValueError("The mentoring document structure changed")
    if paragraphs[77].strip() != "УПРАВЛЕНИЕ СИСТЕМОЙ НАСТАВНИЧЕСТВА В СФЕРЕ ТРУДА":
        raise ValueError("The mentoring document structure changed")

    return {
        MATERIALS / "mentoring-system.txt": mentoring_material(
            paragraphs, 44, 76, "определение и система наставничества"
        ),
        MATERIALS / "mentoring-roles.txt": mentoring_material(
            paragraphs, 77, 92, "управление системой наставничества"
        ),
        MATERIALS / "employment-contract.txt": employment_material(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", action="store_true", help="Verify the checked-in extracts"
    )
    args = parser.parse_args()

    verify_sources()
    materials = build_materials()
    if not args.check:
        MATERIALS.mkdir(parents=True, exist_ok=True)

    for output, content in materials.items():
        encoded = content.encode("utf-8")
        if len(encoded) > 32_000:
            raise ValueError(f"Material exceeds the MAX text limit: {output.name}")
        if args.check:
            if output.read_bytes() != encoded:
                raise ValueError(f"Extract differs from its source: {output.name}")
        else:
            output.write_bytes(encoded)
        print(f"{output.name}: {len(encoded)} bytes")


if __name__ == "__main__":
    main()
