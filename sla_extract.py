"""
Read a scanned SLA PDF with OCR and store its contents by keyword.

Outputs
  sla_data.json   -> {"sections": {...}, "keywords": {...}, "penalties": [...], "formulas": {...}}
  sla_raw.txt     -> full OCR text

Usage
  python sla_extract.py SLA.pdf
  python sla_extract.py SLA.pdf --search penalty delay
"""
import argparse
import json
import re
import sys
from collections import defaultdict

try:
    from .pdf_reader import extract_pdf_pages
except ImportError:
    from pdf_reader import extract_pdf_pages

# Keywords you want to index. Edit freely.
KEYWORDS = [
    "buyer", "service provider", "penalty", "penalties", "deduction", "delay",
    "shortfall", "defect", "damage", "terminate", "termination", "breach",
    "payment", "invoice", "force majeure", "amendment", "contract value",
    "subcontract", "sample", "quantity", "timeline", "price variation",
    "formula", "add-on", "gem", "sdac",
]

# Section headings look like "7. Deductions and Liquidity Damages"
HEADING_RE = re.compile(r"^\s*(\d{1,2})\.\s+([A-Z][A-Za-z&/ ,\-]{3,60})\s*$")


def ocr_pdf(path, dpi=300):
    """Extract PDF text with text-first reading and portable RapidOCR fallback."""
    page_results = extract_pdf_pages(path, minimum_text_chars=40)
    return [str(page["text"]) for page in page_results]


def split_sections(pages):
    """Return {'1. Agreement Overview': text, ...} plus page numbers."""
    sections, current = {}, "Preamble"
    sections[current] = {"pages": [1], "text": []}
    for pno, page in enumerate(pages, start=1):
        for line in page.splitlines():
            m = HEADING_RE.match(line)
            if m:
                current = f"{m.group(1)}. {m.group(2).strip()}"
                sections.setdefault(current, {"pages": [], "text": []})
            sec = sections[current]
            if pno not in sec["pages"]:
                sec["pages"].append(pno)
            if line.strip():
                sec["text"].append(line.strip())
    return {k: {"pages": v["pages"], "text": " ".join(v["text"])} for k, v in sections.items()}


def index_keywords(sections):
    """keyword -> list of {section, sentence}"""
    index = defaultdict(list)
    for title, sec in sections.items():
        sentences = re.split(r"(?<=[.;:])\s+", sec["text"])
        for s in sentences:
            low = s.lower()
            for kw in KEYWORDS:
                if kw in low:
                    index[kw].append({"section": title, "sentence": s.strip()})
    return dict(index)


def extract_penalties(text):
    """Pull '<n>% ...' style deductions out of the OCR text."""
    out = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*%[^.\n]{0,60}", text):
        out.append({"percent": float(m.group(1)), "context": m.group(0).strip()})
    return out


def extract_formulas(text):
    formulas = {}
    m = re.search(r"Total Contract Value\s*=\s*([^\n,]+)", text, re.I)
    if m:
        formulas["total_contract_value"] = m.group(1).strip()
    m = re.search(r"Add-?Ons? Value\s*=\s*([^\n]+)", text, re.I)
    if m:
        formulas["add_ons_value"] = m.group(1).strip()
    return formulas


def search(data, terms):
    for t in terms:
        hits = data["keywords"].get(t.lower(), [])
        print(f"\n=== {t} ({len(hits)} hits) ===")
        for h in hits:
            print(f"[{h['section']}] {h['sentence']}")


def run_streamlit(set_page_config=True):
    """Streamlit interface for uploading and extracting an SLA PDF."""
    import hashlib
    import tempfile
    from pathlib import Path

    import pandas as pd
    import streamlit as st

    if set_page_config:
        st.set_page_config(
            page_title="SLA Extractor",
            page_icon="📄",
            layout="wide",
        )

    st.title("📄 SLA PDF Extractor")
    st.caption("OCR an SLA PDF, split its sections, and inspect indexed keywords and penalties.")

    uploaded = st.file_uploader(
        "Upload an SLA PDF",
        type=["pdf"],
        accept_multiple_files=False,
        key="sla_pdf_upload",
    )
    if uploaded is None:
        st.info("Choose an SLA PDF to begin.")
        return

    content = uploaded.getvalue()
    content_hash = hashlib.sha256(content).hexdigest()

    if st.button("Extract SLA", type="primary", use_container_width=True):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / uploaded.name
            pdf_path.write_bytes(content)
            try:
                with st.spinner("Running OCR... This can take a few minutes for scanned PDFs."):
                    pages = ocr_pdf(str(pdf_path))
            except Exception as exc:
                st.error(f"Could not OCR this PDF: {exc}")
                st.caption("The PDF could not be read. Try a PDF with a readable text layer or a clear scan.")
                return

        full_text = "\n".join(pages)
        sections = split_sections(pages)
        deduction_text = " ".join(
            section["text"]
            for title, section in sections.items()
            if "Deduction" in title
        )
        data = {
            "sections": sections,
            "keywords": index_keywords(sections),
            "penalties": extract_penalties(deduction_text),
            "formulas": extract_formulas(full_text),
        }

        st.session_state["sla_extraction"] = {
            "hash": content_hash,
            "filename": uploaded.name,
            "page_count": len(pages),
            "raw_text": full_text,
            "data": data,
        }

    result = st.session_state.get("sla_extraction")
    if not result or result["hash"] != content_hash:
        return

    data = result["data"]
    full_text = result["raw_text"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pages OCR'd", result["page_count"])
    c2.metric("Sections", len(data["sections"]))
    c3.metric("Indexed keywords", len(data["keywords"]))
    c4.metric("Penalties found", len(data["penalties"]))

    if data["formulas"]:
        st.subheader("Formulas")
        st.dataframe(
            pd.DataFrame(
                [{"Formula": name, "Value": value} for name, value in data["formulas"].items()]
            ),
            use_container_width=True,
            hide_index=True,
        )

    if data["penalties"]:
        st.subheader("Penalties")
        st.dataframe(
            pd.DataFrame(data["penalties"]),
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("Section content")
    selected_section = st.selectbox("Section", list(data["sections"]))
    section = data["sections"][selected_section]
    st.caption(f"Pages: {', '.join(map(str, section['pages']))}")
    st.text_area(
        "Extracted section text",
        value=section["text"],
        height=220,
        key=f"sla_section_{selected_section}",
    )

    st.subheader("Keyword index")
    keyword_rows = [
        {"Keyword": keyword, "Section": hit["section"], "Sentence": hit["sentence"]}
        for keyword, hits in data["keywords"].items()
        for hit in hits
    ]
    if keyword_rows:
        keyword_df = pd.DataFrame(keyword_rows)
        search_keyword = st.text_input(
            "Filter indexed keywords",
            placeholder="e.g. penalty, delay, termination",
        )
        if search_keyword.strip():
            keyword_df = keyword_df[
                keyword_df["Keyword"].str.contains(search_keyword.strip(), case=False, regex=False)
            ]
        st.dataframe(keyword_df, use_container_width=True, hide_index=True)
    else:
        st.info("No configured keywords were found.")

    st.subheader("Downloads")
    download_json = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    st.download_button(
        "Download extracted SLA JSON",
        data=download_json,
        file_name=f"{Path(result['filename']).stem}_sla_data.json",
        mime="application/json",
    )
    st.download_button(
        "Download raw OCR text",
        data=full_text.encode("utf-8"),
        file_name=f"{Path(result['filename']).stem}_sla_raw.txt",
        mime="text/plain",
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--search", nargs="*", help="keywords to look up after extraction")
    args = ap.parse_args()

    pages = ocr_pdf(args.pdf)
    full = "\n".join(pages)
    open("sla_raw.txt", "w", encoding="utf-8").write(full)

    sections = split_sections(pages)
    deduction_text = " ".join(
        s["text"] for t, s in sections.items() if "Deduction" in t
    )
    data = {
        "sections": sections,
        "keywords": index_keywords(sections),
        "penalties": extract_penalties(deduction_text),
        "formulas": extract_formulas(full),
    }
    with open("sla_data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Pages: {len(pages)} | Sections: {len(sections)} | "
          f"Keywords indexed: {len(data['keywords'])}")
    if args.search:
        search(data, args.search)


if __name__ == "__main__":
    sys.exit(main())
