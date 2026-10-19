"""Текст из файлов к заданию: презентации, документы, PDF.

pptx и docx — это zip с XML, читаем их без сторонних библиотек. PDF — через
pypdf, если он установлен; без него файл просто пропускается. Картинки
провайдер модели пока не принимает, поэтому от них только имя файла.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from xml.etree import ElementTree

logger = logging.getLogger("netschoolbot.solver")

# На файл: презентация на 30 слайдов — это 5–10 тысяч символов.
MAX_CHARS = 15000

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _slide_number(name: str) -> int:
    match = re.search(r"(\d+)\.xml$", name)
    return int(match.group(1)) if match else 0


def _pptx(payload: bytes) -> str:
    out = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        slides = sorted((n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=_slide_number)
        for name in slides:
            root = ElementTree.fromstring(archive.read(name))
            lines = []
            for paragraph in root.iter(f"{_A}p"):
                text = "".join(t.text or "" for t in paragraph.iter(f"{_A}t")).strip()
                if text:
                    lines.append(text)
            if lines:
                out.append(f"[Слайд {_slide_number(name)}]\n" + "\n".join(lines))
    return "\n\n".join(out)


def _docx(payload: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    lines = []
    for paragraph in root.iter(f"{_W}p"):
        text = "".join(t.text or "" for t in paragraph.iter(f"{_W}t")).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def _pdf(payload: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    reader = PdfReader(io.BytesIO(payload))
    out, size = [], 0
    for page in reader.pages:
        text = page.extract_text() or ""
        out.append(text)
        size += len(text)
        if size > MAX_CHARS:
            break
    return "\n".join(out)


def _plain(payload: bytes) -> str:
    for encoding in ("utf-8", "cp1251"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


READERS = {
    ".pptx": _pptx,
    ".docx": _docx,
    ".pdf": _pdf,
    ".txt": _plain,
    ".md": _plain,
    ".csv": _plain,
}


def extract_text(name: str, payload: bytes) -> str:
    """Текст файла или "" — если формат не знаем или файл битый."""
    suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    reader = READERS.get(suffix)
    if reader is None:
        return ""
    try:
        text = reader(payload)
    except Exception as exc:  # noqa: BLE001 — битый файл не должен ронять решение
        logger.info("Не прочитан %s: %s: %s", name, type(exc).__name__, exc)
        return ""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n…(дальше обрезано)"
    return text
