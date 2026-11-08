"""
Extract {section, label, value} rows from real GeM contract PDFs
(goods contracts and service/AMC contracts).

Usage:
    python gem_extract.py contract.pdf            # print grid
    python gem_extract.py contract.pdf --json out.json --csv out.csv
"""
import re
import sys
import json
import csv
import collections
import argparse
import io
import pdfplumber
import pandas as pd

from pdf_reader import extract_pdf_pages

HEAD_SIZE = 7.8          # section headings
BODY_MAX = 6.5           # box text (labels/values) is 6.0
SPLIT_X = 302            # left box | right box
SKIP_SECTIONS = re.compile(r"Terms and Conditions|SLA Details|ePBG|Special Terms", re.I)

CID = re.compile(r"\(cid:\d+\)")
DEVA = re.compile(r"[\u0900-\u097F]+")
LABEL_RE = re.compile(r"\|\s*([A-Za-z][A-Za-z0-9 /&.,()\-]*?)\s*:\s*")


# ----------------------------------------------------------------- text utils
def clean(s):
    s = CID.sub("", s or "")
    s = DEVA.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def extract_text_fallback(path):
    """Best-effort extraction for modern/layered GeM PDF layouts."""
    rows = []
    headings = re.compile(
        r"^(ORGANISATION DETAILS|BUYER DETAILS|FINANCIAL APPROVAL DETAILS|"
        r"PAYING AUTHORITY DETAILS|SELLER DETAILS|PRODUCT DETAILS|"
        r"CONSIGNEE DETAILS|CONSIGNEE ADDRESS|PRODUCT SPECIFICATION|"
        r"DELIVERY & ACCEPTANCE|TERMS AND CONDITIONS|DELIVERY CONDITIONS)$",
        re.I,
    )
    standalone_labels = {
        "Type", "Ministry", "Department", "Organisation Name", "Office Zone",
        "Buyer", "Service Provider",
        "Designation", "Contact No.", "Email ID", "GSTIN", "Address",
        "IFD Concurrence", "Administrative Approval", "Financial Approval",
        "Role", "Payment Mode", "GeM Seller ID", "Company Name",
        "MSME Registration No.", "MSE Social Category / Gender",
        "Delivery Instructions", "Consignee / Designation", "Contact / Email",
        "Item Description", "Ordered Qty", "Unit", "Unit Price", "Tax (INR)",
        "Total Price", "Total Order Value (INR)", "Delivery Start", "Delivery By",
        "Delivery Contact", "Delivery Note", "Description of Stores", "Processor",
        "RAM", "Storage", "Display", "Operating System", "Connectivity",
        "Keyboard / Mouse", "Warranty", "Installation", "Country of Origin",
        "Delivery Period", "Inspection", "Acceptance",
    }
    seen = set()
    section = "Header"

    def add(label, value, current_section=None):
        label = clean(label)
        value = clean(value)
        if not label or not value:
            return
        key = (current_section or section, label.lower(), value.lower())
        if key in seen:
            return
        seen.add(key)
        rows.append({
            "section": current_section or section,
            "label": label,
            "value": value,
        })

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines = [clean(line) for line in text.splitlines()]
            for index, line in enumerate(lines):
                if headings.match(line):
                    section = headings.match(line).group(1).title()
                    continue

                if ":" in line:
                    label, value = line.split(":", 1)
                    label = clean(label)
                    value = clean(value)
                    if label and value and len(label) <= 80:
                        add(label, value)

                contract_match = re.search(
                    r"Contract\s*No\.?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-]+)",
                    line,
                    re.I,
                )
                date_match = re.search(
                    r"Generated\s*Date\s*[:\-]?\s*(\d{1,2}-[A-Za-z]+-\d{4})",
                    line,
                    re.I,
                )
                if contract_match:
                    add("Contract No.", contract_match.group(1))
                if date_match:
                    add("Generated Date", date_match.group(1))

                total_match = re.search(
                    r"Total\s+Order\s+Value\s*\(INR\)\s*([0-9][0-9,]*(?:\.\d{1,2})?)",
                    line,
                    re.I,
                )
                if total_match:
                    add("Total Order Value (INR)", total_match.group(1))

                if line in standalone_labels:
                    values = []
                    for next_line in lines[index + 1:index + 5]:
                        if headings.match(next_line) or next_line in standalone_labels:
                            break
                        if ":" in next_line:
                            break
                        if len(values) >= 2:
                            break
                        values.append(next_line)
                    add(line, " ".join(values))

            for table in page.extract_tables():
                table_rows_list = [[clean(cell) for cell in row] for row in table if row]
                if len(table_rows_list) < 2:
                    continue
                header = table_rows_list[0]
                for row in table_rows_list[1:]:
                    if len(row) != len(header):
                        continue
                    for label, value in zip(header, row):
                        if label and value and not re.fullmatch(r"\d+", value):
                            add(f"{section} > {label}", value)

    return rows


def english(s):
    """'Hindi|English' -> 'English' (Hindi glyphs are garbled, English is clean)."""
    s = (s or "").replace("\n", " ")
    if "|" in s:
        s = s.split("|")[-1]
    return clean(s).rstrip(":").strip()


def inline_pairs(text):
    """Find every '|Label : value' in a string. Handles several pairs per line."""
    t = CID.sub("", (text or "").replace("\n", " "))
    ms = list(LABEL_RE.finditer(t))
    out = []
    for i, m in enumerate(ms):
        last = i + 1 == len(ms)
        val = t[m.end(): len(t) if last else ms[i + 1].start()]
        if not last:  # trailing Hindi text belongs to the next label
            val = re.sub(r"[\u0900-\u097F][^|]*$", "", val)
        out.append((m.group(1).strip(), clean(val)))
    if not out:  # e.g. '... name of - Buyer'  /  '... Addons(INR) 12990'
        m = re.search(r"\|\s*([A-Za-z][^|]*?)\s+-\s+([^-|]+)$", t)
        if m:
            out.append((clean(m.group(1)), clean(m.group(2))))
        else:
            m = re.search(r"\|\s*([A-Za-z][^|]*?)\s+([\d,]+(?:\.\d+)?)\s*$", t)
            if m:
                out.append((clean(m.group(1)), m.group(2)))
    return out


def group_lines(words, tol=2.5):
    """Group words into visual lines; returns list of (top, [words sorted by x])."""
    lines = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(w["top"] - lines[-1][0]) <= tol:
            lines[-1][1].append(w)
        else:
            lines.append([w["top"], [w]])
    return [(t, sorted(ws, key=lambda w: w["x0"])) for t, ws in lines]


# ------------------------------------------------------------ box (KV) parser
def parse_box(words):
    """Two-column 'label : value' box. Labels left, values right, same font."""
    words = [w for w in words if w["size"] <= BODY_MAX]
    if not words:
        return []

    # value column x = x of the word that follows a colon on the same line
    cands = []
    for _, ws in group_lines(words):
        for a, b in zip(ws, ws[1:]):
            if a["text"].endswith(":") and b["x0"] - a["x1"] > 3:
                cands.append(b["x0"])
                break
    if not cands:
        return []
    value_x = min(cands) - 1.5

    # label units: tokens up to a word ending with ':'
    units, cur = [], []
    for _, ws in group_lines([w for w in words if w["x0"] < value_x]):
        for w in ws:
            cur.append(w)
            if w["text"].endswith(":"):
                units.append(cur)
                cur = []
    parsed = []
    for u in units:
        text = " ".join(w["text"] for w in u)
        tops = [w["top"] for w in u]
        parsed.append({"label": english(text), "c": (min(tops) + max(tops)) / 2, "vals": []})
    if not parsed:
        return []

    # value lines (values are vertically centred on their label)
    vlines = []
    for top, ws in group_lines([w for w in words if w["x0"] >= value_x]):
        vlines.append({"y": top, "text": " ".join(w["text"] for w in ws)})

    # assign contiguous groups of value lines to units in order (DP)
    n, m, INF, EMPTY = len(parsed), len(vlines), 1e9, 12.0
    dp = [[INF] * (m + 1) for _ in range(n + 1)]
    bk = [[0] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0
    for i in range(1, n + 1):
        for j in range(m + 1):
            for k in range(j + 1):
                if dp[i - 1][k] >= INF:
                    continue
                if k == j:
                    c = EMPTY
                else:
                    ys = [v["y"] for v in vlines[k:j]]
                    c = abs(sum(ys) / len(ys) - parsed[i - 1]["c"])
                if dp[i - 1][k] + c < dp[i][j]:
                    dp[i][j], bk[i][j] = dp[i - 1][k] + c, k
    j = m
    for i in range(n, 0, -1):
        k = bk[i][j]
        parsed[i - 1]["vals"] = [v["text"] for v in vlines[k:j]]
        j = k
    return [(p["label"], clean(" ".join(p["vals"]))) for p in parsed]


# ----------------------------------------------------------------- tables
def table_rows(tbl):
    return [[(c or "") for c in row] for row in tbl.extract()]


def is_header_row(row):
    ne = [c for c in row if c.strip()]
    if len(ne) < 2:
        return False
    bi = sum("|" in c for c in ne)
    return bi == len(ne) or (len(ne) >= 3 and bi / len(ne) >= 0.6)


def flat_from_table(section, rows, header, notes):
    out = []
    # key/value specification table: [Specification | Sub-Spec | Value]
    hn = [english(h) for h in header] if header else []
    if header and len(hn) >= 3 and hn[-1].lower() == "value":
        group = ""
        for r in rows:
            if r[0].strip():
                group = clean(r[0])
            label, val = clean(r[-2]), clean(r[-1])
            if label or val:
                out.append((f"{section} - {group}" if group else section, label, val))
        return out

    if header:
        def is_total(r):
            ne = [c for c in r if c.strip()]
            return len(ne) <= 3 and english(ne[0]).lower().startswith("total")
        data = [r for r in rows if any(c.strip() for c in r) and not is_total(r)]
        for r in rows:                                   # 'Total Order Value' style rows
            if any(c.strip() for c in r) and is_total(r):
                ne = [c for c in r if c.strip()]
                out.append((section, english(ne[0]), clean(ne[-1])))
        multi = len(data) > 1
        for ri, r in enumerate(data, 1):
            sfx = f" [row {ri}]" if multi else ""
            for j, cell in enumerate(r):
                if not cell.strip():
                    continue
                h = hn[j] if j < len(hn) and hn[j] else f"col{j + 1}"
                pairs = inline_pairs(cell)
                if pairs:
                    for lb, vl in pairs:
                        out.append((section, f"{h} > {lb}{sfx}", vl))
                else:
                    out.append((section, f"{h}{sfx}", clean(cell)))
        return out

    # no header: rows of label/value, or inline text
    sub = None
    for r in rows:
        ne = [c for c in r if c.strip()]
        if not ne:
            continue
        if len(ne) >= 3 and english(ne[0]).lower() == "description":
            sub = r                                  # column titles for the rows below
            out.append((section, "Description", " | ".join(clean(c) for c in ne[1:])))
            continue
        if sub and len(ne) >= 3:
            out.append((section, english(r[0]), clean(r[1])))
            for j in range(2, len(r)):
                if r[j].strip() and j < len(sub) and sub[j].strip():
                    out.append((section, clean(sub[j]), clean(r[j])))
            continue
        if len(ne) == 1:
            pairs = inline_pairs(ne[0])
            if pairs:
                out += [(section, lb, vl) for lb, vl in pairs]
            elif "|" in ne[0]:
                out.append((section, english(ne[0]), ""))
            else:
                out.append((section, "", clean(ne[0])))
        else:
            out.append((section, english(ne[0]), " | ".join(clean(c) for c in ne[1:])))
    return out


# ------------------------------------------------------------------ main
def prep(page):
    return page.dedupe_chars(tolerance=1)


def find_headings(p):
    """Return list of dicts {top, x_range, title} (merged multi-line headings)."""
    words = [w for w in p.extract_words(extra_attrs=["size"]) if abs(w["size"] - HEAD_SIZE) < 0.2]
    heads = []
    for top, ws in group_lines(words):
        gaps = [(b["x0"] - a["x1"], i) for i, (a, b) in enumerate(zip(ws, ws[1:]))]
        big = [g for g in gaps if g[0] > 40 and ws[g[1] + 1]["x0"] >= SPLIT_X - 10]
        parts = [ws]
        if big:
            i = big[0][1]
            parts = [ws[: i + 1], ws[i + 1:]]
        for part in parts:
            title = english(" ".join(w["text"] for w in part))
            if heads and abs(top - heads[-1]["top"]) < 16 and not big \
                    and heads[-1]["full"] and not title.lower().startswith(("service", "product details")) \
                    and "|" not in " ".join(w["text"] for w in part) \
                    and heads[-1]["raw_has_pipe"] is False:
                heads[-1]["title"] += " " + title      # wrapped heading line
                continue
            heads.append({
                "top": top,
                "x0": part[0]["x0"],
                "full": not big,
                "title": title,
                "raw_has_pipe": "|" in " ".join(w["text"] for w in part),
            })
    return heads


def extract(path):
    rows = []
    cur_section = "Header"
    pending_header = None   # header carried across a page break

    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages):
            p = prep(page)
            words = p.extract_words(extra_attrs=["size", "fontname"])
            heads = find_headings(p)

            # row bands: (y0, y1, column, title)
            tops = sorted({round(h["top"]) for h in heads})
            segs = []
            first = tops[0] if tops else p.height
            segs.append({"y0": 0, "y1": first - 1, "col": None, "title": cur_section, "cont": True})
            for h in heads:
                nxt = [t for t in tops if t > round(h["top"]) + 1]
                y1 = (nxt[0] - 1) if nxt else p.height
                col = None if h["full"] else ("L" if h["x0"] < SPLIT_X else "R")
                segs.append({"y0": h["top"], "y1": y1, "col": col, "title": h["title"], "cont": False})

            for s in segs:
                title = s["title"]
                if pno == 0 and s["cont"]:
                    title = "Header"
                title = re.sub(r"^Product Specification.*", "Product Specification", title)
                if not s["cont"]:
                    cur_section = title
                if SKIP_SECTIONS.search(title) or s["y1"] <= s["y0"]:
                    continue

                seg_words = [w for w in words
                             if s["y0"] - 1 <= w["top"] < s["y1"] and abs(w["size"] - HEAD_SIZE) >= 0.2]
                if s["col"] == "L":
                    seg_words = [w for w in seg_words if w["x0"] < SPLIT_X]
                elif s["col"] == "R":
                    seg_words = [w for w in seg_words if w["x0"] >= SPLIT_X]

                # ---- table path (full-width segments)
                tables = []
                if s["col"] is None and title != "Header":
                    crop = p.crop((0, max(0, s["y0"] - 2), p.width, min(p.height, s["y1"])))
                    tables = [t for t in crop.find_tables()
                              if max(len(r) for r in t.extract()) >= 2]
                if tables:
                    used = []
                    for t in tables:
                        r = table_rows(t)
                        used.append(t.bbox)
                        header = None
                        if r and is_header_row(r[0]):
                            header, r = r[0], r[1:]
                        elif pending_header and len(r[0]) == len(pending_header):
                            header = pending_header
                        # header continuing from previous page (words above this table)
                        if header is pending_header and pending_header:
                            above = [w for w in words if w["top"] < t.bbox[1] and w["top"] >= 0
                                     and w["size"] >= 6.5 and not s["cont"] is False]
                            if s["cont"] and above and t.rows and t.rows[0].cells:
                                hh = list(header)
                                for j, cb in enumerate(t.rows[0].cells):
                                    if cb:
                                        txt = " ".join(w["text"] for w in above if cb[0] <= w["x0"] < cb[2])
                                        hh[j] = (hh[j] + " " + txt).strip()
                                header = hh
                        if header and not any(any(c.strip() for c in x) for x in r):
                            pending_header = header          # header only; data on next page
                            continue
                        rows += flat_from_table(title, r, header, None)
                    # text outside the tables (e.g. 'BIS license of the product: ...')
                    rest = [w for w in seg_words if w["size"] >= 6.5 and not any(
                        b[0] - 1 <= w["x0"] <= b[2] and b[1] - 1 <= w["top"] <= b[3] for b in used)]
                    for _, ws in group_lines(rest):
                        for lb, vl in inline_pairs(" ".join(w["text"] for w in ws)):
                            rows.append((title, lb, vl))
                    continue

                # ---- key/value box + inline lines
                for lb, vl in parse_box(seg_words):
                    rows.append((title, lb, vl))
                big = [w for w in seg_words if w["size"] > BODY_MAX]
                last = None
                for _, ws in group_lines(big):
                    text = " ".join(w["text"] for w in ws)
                    pairs = inline_pairs(text)
                    if pairs:
                        for lb, vl in pairs:
                            rows.append((title, lb, vl))
                        last = len(rows) - 1
                    elif last is not None and "|" not in text and title == "Header":
                        sec, lb, vl = rows[last]            # wrapped header value
                        rows[last] = (sec, lb, clean(vl + " " + text))
    extracted = [{"section": a, "label": b, "value": c} for a, b, c in rows]
    normalized_extracted = []
    for row in extracted:
        if row["label"].lower() == "contract no." and "generated date" in row["value"].lower():
            contract_match = re.search(
                r"([A-Z0-9][A-Z0-9\-]+)",
                row["value"],
                re.I,
            )
            if contract_match:
                row = {**row, "value": contract_match.group(1)}
        normalized_extracted.append(row)
    extracted = normalized_extracted

    # Modern GeM contracts use layouts that can bypass the coordinate parser.
    # Add text/table fallback rows without replacing any structured matches.
    fallback = extract_text_fallback(path)
    seen = {
        (row["section"].lower(), row["label"].lower(), row["value"].lower())
        for row in extracted
    }
    for row in fallback:
        if row["label"].lower() == "contract no." and "generated date" in row["value"].lower():
            contract_match = re.search(
                r"([A-Z0-9][A-Z0-9\-]+)",
                row["value"],
                re.I,
            )
            if contract_match:
                row = {**row, "value": contract_match.group(1)}
        key = (
            row["section"].lower(),
            row["label"].lower(),
            row["value"].lower(),
        )
        if key not in seen:
            extracted.append(row)
            seen.add(key)

    # Scanned/image-only PDFs have no pdfplumber coordinates. Merge generic
    # OCR label/value lines so they can be consumed by the keyword/value search.
    ocr_pages = extract_pdf_pages(path)
    for page in ocr_pages:
        for raw_line in str(page["text"]).splitlines():
            line = clean(raw_line)
            if ":" not in line:
                continue
            label, value = (part.strip() for part in line.split(":", 1))
            if not label or not value or len(label) > 80:
                continue
            key = ("ocr", label.lower(), value.lower())
            if key not in seen:
                extracted.append({"section": "OCR", "label": label, "value": value})
                seen.add(key)
    return extracted


def run_streamlit():
    import streamlit as st

    st.set_page_config(page_title="GeM PDF Extractor", layout="wide")
    st.title("GeM PDF Extractor")

    uploaded = st.file_uploader(
        "Upload one PDF file", type=["pdf"], accept_multiple_files=False
    )
    if uploaded is None:
        st.info("Choose a PDF to begin.")
        return

    try:
        pdf_source = io.BytesIO(uploaded.getvalue())
        data = extract(pdf_source)
    except Exception as exc:
        st.error(f"Could not extract this PDF: {exc}")
        return

    df = pd.DataFrame(data, columns=["section", "label", "value"])
    st.success(f"Extracted {len(df)} rows from {uploaded.name}")
    st.dataframe(df, use_container_width=True, hide_index=True)


def run_cli(argv):
    parser = argparse.ArgumentParser(
        description="Extract section, label, and value rows from a GeM contract PDF"
    )
    parser.add_argument("pdf", help="Path to the input PDF")
    parser.add_argument("--json", metavar="FILE", help="Write JSON results")
    parser.add_argument("--csv", metavar="FILE", help="Write CSV results")
    args = parser.parse_args(argv)

    data = extract(args.pdf)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as output:
            json.dump(data, output, indent=2, ensure_ascii=False)
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as output:
            writer = csv.DictWriter(
                output, fieldnames=["section", "label", "value"]
            )
            writer.writeheader()
            writer.writerows(data)

    for row in data:
        print(
            f'[{row["section"]}] {row["label"]} -> {row["value"][:90]}'
        )


if __name__ == "__main__":
    if "streamlit.runtime.scriptrunner_utils" in sys.modules:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        if get_script_run_ctx() is not None:
            run_streamlit()
        else:
            run_cli(sys.argv[1:])
    else:
        run_cli(sys.argv[1:])
