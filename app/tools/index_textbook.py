"""Подготовить учебник для решений: PDF → <id>.pages.json.

    python -m app.tools.index_textbook учебник.pdf algebra9 [--out data/textbooks]

Берёт текстовый слой PDF (pypdf), оглавление из закладок, сам находит, как
номер pdf-страницы соотносится с напечатанным, и где начинаются «Ответы».
Скан без текстового слоя сперва распознаётся (tools/textbooks/ocr.swift) и
передаётся через --lines; картинки его страниц — в <out>/<id>/ (render.swift).

После — добавить книгу в library.json рядом (см. app/solver/textbooks.py).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path


def _page_number(text: str) -> int | None:
    """Напечатанный номер: отдельное число в первых или последних строках."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    for line in lines[:2] + lines[-2:]:
        if re.fullmatch(r"\d{1,3}", line):
            return int(line)
    return None


def detect_offset(pages: list[str]) -> int:
    votes = Counter(n - i for i, text in enumerate(pages) if (n := _page_number(text)) is not None)
    if not votes:
        return 1
    offset, count = votes.most_common(1)[0]
    return offset if count >= 3 else 1


def detect_answers(pages: list[str]) -> int | None:
    """Первая страница раздела «Ответы» в последней трети книги."""
    heading = re.compile(r"(?m)^\s*(ОТВЕТЫ|Ответы)(\s+и\s+указания|\s+к\s+упражнениям|\s+к\s+задачам)?\s*$")
    for index in range(len(pages) * 2 // 3, len(pages)):
        if heading.search(pages[index]):
            return index
    return None


def _outline(reader) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []

    def walk(items) -> None:
        for item in items:
            if isinstance(item, list):
                walk(item)
                continue
            try:
                page = reader.get_destination_page_number(item)
                if isinstance(page, int):
                    out.append((str(item.title).strip(), page))
            except Exception:  # noqa: BLE001 — битая закладка не мешает остальным
                continue

    try:
        walk(reader.outline)
    except Exception:  # noqa: BLE001
        pass
    return out


def index_pdf(path: Path, lines: list | None = None) -> dict:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if lines is not None:
        # Скан: текст — из распознавания, из PDF — только закладки.
        pages = ["\n".join(str(row[0]) for row in page) for page in lines]
    else:
        pages = [(page.extract_text() or "").replace("­", "") for page in reader.pages]
    return {
        "title": (reader.metadata.title if reader.metadata and reader.metadata.title else path.stem),
        "offset": detect_offset(pages),
        "answers_from": detect_answers(pages),
        "toc": _outline(reader),
        "pages": pages,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("id", help="имя книги латиницей, например algebra9")
    parser.add_argument("--out", type=Path, default=Path("data/textbooks"))
    parser.add_argument("--lines", type=Path, help="распознанный скан (tools/textbooks/ocr.swift)")
    args = parser.parse_args(argv)

    lines = json.loads(args.lines.read_text("utf-8")) if args.lines else None
    index = index_pdf(args.pdf, lines)
    args.out.mkdir(parents=True, exist_ok=True)
    if lines is not None:
        (args.out / f"{args.id}.lines.json").write_text(json.dumps(lines, ensure_ascii=False), "utf-8")
    target = args.out / f"{args.id}.pages.json"
    target.write_text(json.dumps(index, ensure_ascii=False), "utf-8")

    empty = sum(1 for text in index["pages"] if len(text.strip()) < 40)
    print(f"{target}: страниц {len(index['pages'])}, без текста {empty}, "
          f"сдвиг нумерации {index['offset']:+d}, ответы с pdf-страницы "
          f"{index['answers_from'] if index['answers_from'] is not None else '—'}, "
          f"закладок {len(index['toc'])}")
    if empty > len(index["pages"]) // 3:
        print("Похоже на скан без текстового слоя — распознайте его: tools/textbooks/ocr.swift.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
