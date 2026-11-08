import sys
import pdfplumber
from collections import defaultdict
import streamlit as st
VALUE_X = {57: 170, 301: 392}  # x where the value column starts, per box (left box, right box)
SIDE_SPLIT = 295  # x where the right-hand box starts (boxes begin at x=57 and x=301)


def keep(w):
    """Drop the rotated watermark, oversized text and the icon glyph."""
    return w["size"] <= 12 and w["fontname"] != "ZapfDingbats" and w.get("upright", True)


def is_bold(w):
    return "bold" in w["fontname"].lower()


def parse_segment(seg, box_x, section, pairs):
    """Parse the words of one line inside one box. Returns the current section."""
    seg = sorted(seg, key=lambda w: w["x0"])
    text = " ".join(w["text"] for w in seg)

    # 1) section heading: all bold + uppercase
    if all(is_bold(w) for w in seg) and text.isupper():
        return text

    # 2) inline "Label: value" (bold label, regular value), e.g. Contract No.
    if any(is_bold(w) for w in seg) and any(not is_bold(w) for w in seg) and ":" in text:
        cur = None
        for w in seg:
            if is_bold(w):
                if cur is None or cur["value"]:
                    cur = {"section": section, "label": "", "value": ""}
                    pairs.append(cur)
                cur["label"] += (" " if cur["label"] else "") + w["text"].rstrip(":")
            elif cur:
                cur["value"] += (" " if cur["value"] else "") + w["text"]
        return section

    # 3) two-column rows: label column vs value column (same font, so use x position)
    left = [w for w in seg if w["x0"] < VALUE_X[box_x]]
    right = [w for w in seg if w["x0"] >= VALUE_X[box_x]]
    if left and right:
        pairs.append({
            "section": section,
            "label": " ".join(w["text"] for w in left),
            "value": " ".join(w["text"] for w in right),
        })
    elif right and pairs:  # wrapped value line
        pairs[-1]["value"] += " " + " ".join(w["text"] for w in right)
    return section


def extract_pairs(path):
    pairs = []
    sections = {"L": None, "R": None}  # separate section tracker per column
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            words = [w for w in page.extract_words(extra_attrs=["fontname", "size", "upright"]) if keep(w)]
            lines = defaultdict(list)
            for w in words:
                lines[round(w["top"] / 3)].append(w)
            for k in sorted(lines):
                line = lines[k]
                left = [w for w in line if w["x0"] < SIDE_SPLIT]
                right = [w for w in line if w["x0"] >= SIDE_SPLIT]
                if left and right and min(w["x0"] for w in right) < SIDE_SPLIT + 20:
                    # two boxes side by side (Organisation | Buyer, etc.)
                    sections["L"] = parse_segment(left, 57, sections["L"], pairs)
                    sections["R"] = parse_segment(right, 301, sections["R"], pairs)
                else:
                    sections["L"] = parse_segment(line, 57, sections["L"], pairs)
    return pairs

st.title("🔎 PDF Keyword & Value Finder")
st.caption("Upload one PDF at a time, search any keyword, and inspect the matching text with page numbers.")

with st.sidebar:
    st.header("How to use")
    st.markdown(
        "1. Upload one PDF.\n"
        "2. Enter a keyword such as `contract`, `seller`, `warranty`, `Canon`, or `value`.\n"
        "3. Review matching snippets and extracted fields.\n"
        "4. Download the results as CSV."
    )
    st.info("Text-based PDFs work best. Scanned/image-only PDFs need OCR.")
   

uploaded = st.file_uploader("Upload one PDF file", type=["pdf"], accept_multiple_files=False)
pages = []

if uploaded is None:
    st.info("Choose a PDF to begin. Only one file is processed at a time.")
    st.stop()
    raise SystemExit

try:
    pages = extract_pairs(uploaded)
except Exception as exc:
    st.error(f"Could not read this PDF: {exc}")
    st.stop()
    raise SystemExit

if not pages:
    st.warning("No pages were found in this PDF.")
    st.stop()
    raise SystemExit


import pandas as pd

df=pd.DataFrame(pages, columns=["section", "label", "value"])
c1, c2, c3 = st.columns(3)
c1.metric("File", uploaded.name)

st.write(df)


st.success(f"Loaded one PDF successfully: {len(pages)} page(s). Upload another file to replace this one.")

# if __name__ == "__main__":
#     path = sys.argv[1] if len(sys.argv) > 1 else "pdf_keyword_search/files/GeM_Contract_Filled_Sample_Dummy_Details.pdf"
#     for p in extract_pairs(path):
#         print(f'[{p["section"]}] {p["label"]} -> {p["value"][:60]}')
#         st.write(f'[{p["section"]}] {p["label"]} -----------> {p["value"][:60]}')