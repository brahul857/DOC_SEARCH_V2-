from pathlib import Path
from typing import Any, Dict, List, Union
from functools import lru_cache

import pymupdf
import re


_ocr_status = {"available": True, "error": ""}


@lru_cache(maxsize=1)
def _ocr_engine():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def _ocr_image(image_bytes: bytes) -> str:
    """OCR one page image with a portable ONNX-based engine."""
    try:
        result, _ = _ocr_engine()(image_bytes)
    except ImportError:
        _ocr_status["available"] = False
        _ocr_status["error"] = "RapidOCR is not installed in the Streamlit environment."
        return ""
    except Exception as first_error:
        _ocr_status["error"] = str(first_error)
        try:
            from PIL import Image, ImageOps, ImageEnhance
            import io

            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            image = ImageOps.autocontrast(image, cutoff=1)
            image = ImageEnhance.Contrast(image).enhance(1.35)
            image = ImageEnhance.Sharpness(image).enhance(1.5)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            result, _ = _ocr_engine()(buffer.getvalue())
        except Exception:
            return ""

    if not result:
        return ""

    lines = []
    for item in result:
        if len(item) >= 2 and item[1]:
            box = item[0]
            top = min(float(point[1]) for point in box)
            left = min(float(point[0]) for point in box)
            lines.append((top, left, str(item[1]).strip()))

    lines.sort(key=lambda line: (line[0], line[1]))
    _ocr_status["available"] = True
    _ocr_status["error"] = ""
    return "\n".join(text for _, _, text in lines)


def get_ocr_status() -> Dict[str, Any]:
    """Return the latest OCR availability and fallback error."""
    return dict(_ocr_status)


def extract_template_field_value(pages: List[Dict[str, Any]], field: str) -> str:
    """Find one template field despite OCR spacing, punctuation, and casing."""
    field = re.sub(r"\s+", " ", str(field)).strip(" \t:-")
    if not field:
        return ""

    field_normalized = re.sub(r"[^a-z0-9]+", "", field.lower())
    field_words = [
        word for word in re.split(r"[^a-z0-9]+", field.lower()) if word
    ]
    for item in pages:
        lines = str(item.get("text", "")).splitlines()
        full_lines = [re.sub(r"\s+", " ", line).strip() for line in lines]

        for index, line in enumerate(full_lines):
            if ":" in line:
                label, value = line.split(":", 1)
            else:
                label, value = line, ""
            label = re.sub(r"^\s*\d+[.)]\s*", "", label).strip(" \t:-")
            if re.sub(r"[^a-z0-9]+", "", label.lower()) == field_normalized:
                value = value.strip(" \t:-")
                if not value and index + 1 < len(full_lines):
                    value = full_lines[index + 1].strip(" \t:-")
                if value:
                    next_label = re.search(
                        r"[A-Z][A-Za-z0-9]*(?:[A-Z][A-Za-z0-9]*|[ ][A-Za-z0-9]+)*:",
                        value,
                    )
                    if next_label:
                        value = value[:next_label.start()]
                    value = re.sub(r"\s+", " ", value).strip(" \t:.-")
                    if value:
                        return value

            if field_words:
                flexible_label = r"\s*[\._\-]?\s*".join(
                    re.escape(word) for word in field_words
                )
                label_match = re.search(
                    rf"(?i){flexible_label}",
                    line,
                )
                if label_match:
                    remainder = line[label_match.end():]
                    remainder = re.sub(r"^\s*[.:\-]+\s*", "", remainder)
                    next_label = re.search(
                        r"[A-Z][A-Za-z0-9]*(?:[A-Z][A-Za-z0-9]*|[ ][A-Za-z0-9]+)*:",
                        remainder,
                    )
                    if next_label:
                        remainder = remainder[:next_label.start()]
                    remainder = re.sub(r"\s+", " ", remainder).strip(" \t:.-")
                    if remainder and index < len(full_lines):
                        return remainder
    return ""


def extract_pdf_pages(
    source: Union[bytes, bytearray, Path, str],
    *,
    ocr: bool = True,
    minimum_text_chars: int = 40,
) -> List[Dict[str, Any]]:
    """Extract PDF text, falling back to OCR for image-only or sparse pages."""
    if isinstance(source, (bytes, bytearray)):
        document = pymupdf.open(stream=bytes(source), filetype="pdf")
    else:
        document = pymupdf.open(str(source))

    pages: List[Dict[str, Any]] = []
    try:
        for page_number, page in enumerate(document, start=1):
            text = page.get_text("text") or ""
            text = text.strip()

            if ocr and len(text) < minimum_text_chars:
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(3, 3), alpha=False)
                ocr_text = _ocr_image(pixmap.tobytes("png"))
                if ocr_text:
                    text = f"{text}\n{ocr_text}".strip()

            pages.append({"page": page_number, "text": text})
    finally:
        document.close()
    return pages
