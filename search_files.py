import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Iterable

from pdf_reader import (
    extract_pdf_pages as extract_shared_pdf_pages,
    extract_template_field_value,
)
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL

connection_url = URL.create(
    "mssql+pyodbc",
    username="rahul",
    password="Rahul2!@#$",   # special characters are handled safely by URL.create
    host="103.184.242.46",         # e.g. "localhost" or "localhost,1433"
    database="GazoleTestDB",
    query={"driver": "ODBC Driver 17 for SQL Server"},
)
engine = create_engine(connection_url)


DEFAULT_FILES_DIR = Path(__file__).resolve().parent / "files"


def format_template_fields(raw_fields: Iterable[str]) -> list[str]:
    fields = []
    seen = set()
    for raw_field in raw_fields:
        if not isinstance(raw_field, str):
            continue
        for field in re.split(r"[,;]", raw_field):
            field = re.sub(r"\s+", " ", field).strip(" \t:-")
            normalized_field = field.lower()
            if field and normalized_field not in seen:
                seen.add(normalized_field)
                fields.append(field)
    return fields


def extract_pdf_pages(pdf_path: Path) -> list[dict[str, object]]:
    return extract_shared_pdf_pages(pdf_path)


def search_pdf(
    pdf_path: Path,
    keywords: Iterable[str],
    context_chars: int = 180,
    case_sensitive: bool = False,
    match_mode: str = "both",
) -> list[dict[str, object]]:
    keyword_patterns = [
        re.compile(re.escape(keyword), 0 if case_sensitive else re.IGNORECASE)
        for keyword in keywords
        if keyword.strip()
    ]
    matches = []

    if match_mode in {"keyword", "both"}:
        for page in extract_pdf_pages(pdf_path):
            text = str(page["text"])
            for pattern in keyword_patterns:
                for match in pattern.finditer(text):
                    start = max(0, match.start() - context_chars)
                    end = min(len(text), match.end() + context_chars)
                    context = re.sub(r"\s+", " ", text[start:end]).strip()
                    matches.append({
                        "File": pdf_path.name,
                        "Match type": "Keyword",
                        "Keyword": match.group(0),
                        "Page": page["page"],
                        "Section": "",
                        "Label": "",
                        "Value": "",
                        "Context": context,
                    })

    if match_mode in {"value", "both"}:
        try:
            from gem_extract import extract

            extracted_rows = extract(pdf_path)
        except Exception:
            extracted_rows = []

        keyword_list = [keyword.strip() for keyword in keywords if keyword.strip()]
        flags = 0 if case_sensitive else re.IGNORECASE
        for row in extracted_rows:
            section = str(row.get("section", ""))
            label = str(row.get("label", ""))
            value = str(row.get("value", ""))
            searchable = f"{label} {value}"
            for keyword in keyword_list:
                if re.search(re.escape(keyword), searchable, flags):
                    matches.append({
                        "File": pdf_path.name,
                        "Match type": "Value",
                        "Keyword": keyword,
                        "Page": "",
                        "Section": section,
                        "Label": label,
                        "Value": value,
                        "Context": f"{label}: {value}",
                    })
                    break
    return matches


def search_files(
    files_dir: Path,
    keywords: Iterable[str],
    context_chars: int = 180,
    case_sensitive: bool = False,
    match_mode: str = "both",
) -> list[dict[str, object]]:
    if not files_dir.exists():
        raise FileNotFoundError(f"Files directory not found: {files_dir}")

    matches = []
    for pdf_path in sorted(files_dir.glob("*.pdf")):
        matches.extend(
            search_pdf(
                pdf_path,
                keywords,
                context_chars=context_chars,
                case_sensitive=case_sensitive,
                match_mode=match_mode,
            )
        )
    return matches


def search_template_fields(
    files_dir: Path,
    fields: Iterable[str],
) -> list[dict[str, object]]:
    """Extract only the selected template fields from every uploaded PDF."""
    field_list = []
    seen_fields = set()
    for field in fields:
        if not isinstance(field, str):
            continue
        field = re.sub(r"\s+", " ", field).strip(" \t:-")
        normalized = re.sub(r"[^a-z0-9]+", "", field.lower())
        if field and normalized and normalized not in seen_fields:
            seen_fields.add(normalized)
            field_list.append(field)
    if not field_list:
        return []

    matches = []
    for pdf_path in sorted(files_dir.glob("*.pdf")):
        try:
            pages = extract_pdf_pages(pdf_path)
        except Exception:
            continue

        for field in field_list:
            found = False
            for page in pages:
                value = extract_template_field_value([page], field)
                if value:
                    matches.append({
                        "File": pdf_path.name,
                        "Match type": "Template field",
                        "Keyword": field,
                        "Page": page["page"],
                        "Section": "",
                        "Label": field,
                        "Value": value,
                        "Context": f"{field}: {value}",
                    })
                    found = True
                if found:
                    break
    return matches


def write_csv(matches: list[dict[str, object]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "File", "PDF File", "Match type", "Keyword", "Page", "Section",
                "Label", "Value", "Context",
            ],
        )
        writer.writeheader()
        writer.writerows(matches)


def search_database(
    database_engine,
    keywords: Iterable[str],
    table_name: str = "PDF_Keyword_Search",
) -> list[dict[str, object]]:
    """Search extracted fields and values stored in SQL Server."""
    keyword_list = [keyword.strip() for keyword in keywords if keyword.strip()]
    if not keyword_list:
        return []

    inspector = inspect(database_engine)
    if not inspector.has_table(table_name):
        raise LookupError(f"Database table '{table_name}' was not found.")

    columns = [column["name"] for column in inspector.get_columns(table_name)]
    if not columns:
        return []

    searchable_columns = [
        column for column in columns
        if column.lower() in {
            "field", "extracted value", "value", "label", "keyword",
        }
    ]
    if not searchable_columns:
        searchable_columns = columns

    quoted_columns = ", ".join(f"[{column.replace(']', ']]')}]" for column in columns)
    select_sql = text(
        f"SELECT {quoted_columns} FROM [{table_name.replace(']', ']]')}]"
    )
    with database_engine.connect() as connection:
        rows = connection.execute(select_sql).mappings().all()

    matches = []
    seen_matches = set()
    for row in rows:
        values = {str(column): row.get(column) for column in columns}
        values_by_lower_name = {
            str(column).lower(): row.get(column)
            for column in columns
        }
        searchable_text = " | ".join(
            str(values.get(column, "") or "")
            for column in searchable_columns
        )
        matched_keywords = [
            keyword for keyword in keyword_list
            if re.search(re.escape(keyword), searchable_text, re.IGNORECASE)
        ]
        if not matched_keywords:
            continue

        pdf_file_name = str(
            values_by_lower_name.get("pdf_file_name")
            or values_by_lower_name.get("pdf_file_path")
            or ""
        ).strip()
        pdf_file_path = str(
            values_by_lower_name.get("pdf_file_path") or pdf_file_name
        ).strip()
        display_file =  pdf_file_path or "(not recorded)"
        for keyword in matched_keywords:
            dedupe_key = (display_file.lower(), keyword.lower())
            if dedupe_key in seen_matches:
                continue
            seen_matches.add(dedupe_key)
            matches.append({
                "File": display_file,
                "PDF File": pdf_file_path  or "",
                "Match type": "Database",
                "Keyword": keyword,
                "Page": "",
                "Section": "",
                "Label": "",
                "Value": "",
                "Context": "",
            })
    return matches


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Search PDF files in a folder for one or more keywords."
    )
    parser.add_argument(
        "keywords",
        nargs="+",
        help="Keywords or phrases to find (use quotes for phrases).",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=DEFAULT_FILES_DIR,
        help=f"Directory containing PDFs (default: {DEFAULT_FILES_DIR})",
    )
    parser.add_argument(
        "--context",
        type=int,
        default=180,
        help="Characters of context around each match (default: 180).",
    )
    parser.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Use case-sensitive keyword matching.",
    )
    parser.add_argument(
        "--mode",
        choices=["keyword", "value", "both"],
        default="both",
        help="Search PDF text, extracted label/value fields, or both.",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        help="Optional CSV output path.",
    )
    return parser


def run_streamlit(set_page_config: bool = True) -> None:
    import pandas as pd
    import streamlit as st

    if set_page_config:
        st.set_page_config(
            page_title="PDF Keyword File Search",
            page_icon="🔎",
            layout="wide",
        )
    st.title("🔎 Database PDF Keyword Search")
    st.caption("Search keywords or values stored in the database and show matching PDF files.")

    files_dir = DEFAULT_FILES_DIR
    with st.form("keyword_search_form"):
        with st.sidebar:
            st.header("Search options")
            template_keywords = []
            template_name = None
            try:
                inspector = inspect(engine)
                if inspector.has_table("Template_master"):
                    with engine.connect() as connection:
                        template_df = connection.execute(
                            text("SELECT * FROM [Template_master]")
                        )
                        templates = pd.DataFrame(
                            template_df.fetchall(),
                            columns=template_df.keys(),
                        )
                    if not templates.empty:
                        template_columns = {
                            re.sub(r"[^a-z0-9]+", "", str(column).lower()).strip(): column
                            for column in templates.columns
                        }
                        name_column = (
                            template_columns.get("templatename")
                            or template_columns.get("template")
                            or template_columns.get("name")
                        )
                        keyword_column = (
                            template_columns.get("keywords")
                            or template_columns.get("keyword")
                        )
                        if name_column and keyword_column:
                            template_options = ["None"] + [
                                str(value).strip()
                                for value in templates[name_column].dropna()
                            ]
                            template_name = st.selectbox(
                                "Search by template",
                                options=template_options,
                            )
                            if template_name != "None":
                                selected_template = templates[
                                    templates[name_column].astype(str) == template_name
                                ].iloc[0]
                                template_keywords = format_template_fields(
                                    [
                                        str(
                                            selected_template[keyword_column] or ""
                                        )
                                    ]
                                )
                                st.caption(
                                    "Template keywords: "
                                    + ", ".join(template_keywords)
                                )
            except Exception as exc:
                st.warning(f"Could not load templates: {exc}")

            keywords_input = ""
            if not template_name or template_name == "None":
                keywords_input = st.text_input(
                    "Keywords",
                    placeholder="warranty, GEMC-SAMPLE",
                )
            search_submitted = st.form_submit_button(
                (
                    "Search Template Fields"
                    if template_name and template_name != "None"
                    else "Search Database"
                ),
                type="primary",
                use_container_width=True,
            )

    if template_name and template_name != "None":
        keywords = template_keywords
    else:
        keywords = [
            keyword.strip()
            for keyword in re.split(r"[,;]", keywords_input)
            if keyword.strip()
        ]

    if not keywords:
        st.info("Enter one or more keywords or values to search the database.")
        return

    if not search_submitted:
        st.info("Enter a keyword and select **Search Database**.")
        return

    try:
        if template_name and template_name != "None":
            matches = search_template_fields(files_dir, keywords)
        else:
            matches = search_database(engine, keywords)
    except Exception as exc:
        st.error(f"Could not search the database: {exc}")
        return

    st.success(f"Found {len(matches)} match(es).")
    if matches:
        matches_df = pd.DataFrame(matches)
        template_search = template_name and template_name != "None"
        if template_search:
            available_columns = [
                column for column in ["File", "Keyword", "Page", "Context", "Value"]
                if column in matches_df.columns
            ]
            display_df = matches_df[available_columns].copy()
        else:
            display_df = matches_df[["Keyword", "File", "PDF File"]].copy()
            display_df["PDF File"] = display_df["PDF File"].fillna("")
            display_df["PDF File"] = display_df["PDF File"].replace("", "(not recorded)")
        database_files = {}
        for match in matches:
            if match.get("Match type") != "Database":
                continue
            saved_file = str(match.get("PDF File") or "").strip()
            original_file = str(match.get("File") or "").strip()
            if saved_file or original_file:
                file_key = saved_file.lower() or original_file.lower()
                database_files.setdefault(
                    file_key,
                    (original_file or Path(saved_file).name, saved_file),
                )

        st.subheader("Search results")
        st.dataframe(display_df, use_container_width=True, hide_index=True)
        if not template_search:
            st.download_button(
                "Download results (CSV)",
                data=display_df.to_csv(index=False).encode("utf-8-sig"),
                file_name="search_results.csv",
                mime="text/csv",
            )

        if template_search:
            matched_files = sorted(
                set(str(match.get("File", "")).strip() for match in matches if match.get("File"))
            )
            if matched_files:
                st.subheader("Matched PDF files")
                for index, file_name in enumerate(matched_files):
                    saved_path = files_dir / file_name
                    if not saved_path.is_file():
                        continue
                    with st.container(border=True):
                        st.markdown(f"**{file_name}**")
                        st.download_button(
                            "Download file",
                            data=saved_path.read_bytes(),
                            file_name=file_name,
                            mime="application/pdf",
                            key=f"template_pdf_download_{index}_{saved_path.name}",
                            use_container_width=True,
                        )

        if database_files:
            st.subheader("Matched PDF files")
            for index, (original_file, saved_file) in enumerate(sorted(database_files.values())):
                saved_path = files_dir / Path(saved_file).name
                if not saved_path.is_file() and saved_file:
                    saved_path = Path(saved_file) if Path(saved_file).is_file() else saved_path

                with st.container(border=True):
                    st.markdown(f"**{original_file or saved_path.name}**")
                    if saved_path.is_file():
                        download_name = original_file or saved_path.name
                        st.download_button(
                            "Download file",
                            data=saved_path.read_bytes(),
                            file_name=download_name,
                            mime="application/pdf",
                            key=f"database_pdf_download_{index}_{saved_path.name}",
                            use_container_width=True,
                        )
                    else:
                        st.caption("File not available locally")
    else:
        st.warning("No matches found. Try a shorter keyword or different spelling.")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        matches = search_database(engine, args.keywords)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.csv:
        try:
            write_csv(matches, args.csv)
        except Exception as exc:
            print(f"Error: Could not write CSV: {exc}", file=sys.stderr)
            return 1

    print(f"Found {len(matches)} match(es).")
    for match in matches:
        print(
            f"{match['Keyword']} -> {match['File']}"
        )
    if args.csv:
        print(f"Results saved to: {args.csv}")
    return 0


if __name__ == "__main__":
    try:
        import streamlit as streamlit_module
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        if get_script_run_ctx() is not None:
            run_streamlit()
        else:
            raise SystemExit(main())
    except ImportError:
        raise SystemExit(main())
