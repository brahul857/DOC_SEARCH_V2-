import re
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, inspect, text


def _column_lookup(dataframe):
    return {
        re.sub(r"[^a-z0-9]+", "", str(column).lower()).strip(): str(column)
        for column in dataframe.columns
    }


def _format_key_fields(raw_keys):
    field_list = []
    seen = set()
    for field in re.split(r"[,;]", raw_keys):
        field = re.sub(r"\s+", " ", field).strip(" \t:-")
        normalized_field = field.lower()
        if field and normalized_field not in seen:
            seen.add(normalized_field)
            field_list.append(field)
    return field_list


def run_template_management(connection_url, files_dir, set_page_config=True):
    if set_page_config:
        st.set_page_config(
            page_title="Template Management",
            page_icon="Template",
            layout="wide",
        )

    st.title("Template Management")
    st.caption("Add and edit template names, keywords, and reference PDFs.")

    engine = create_engine(connection_url, fast_executemany=True)
    table_name = "Template_master"
    inspector = inspect(engine)

    add_tab, edit_tab = st.tabs(["Add Template", "Edit Template"])

    with add_tab:
        with st.form("template_add_page_form", clear_on_submit=True):
            template_name = st.text_input(
                "Template name",
                placeholder="e.g. GeM Contract",
            )
            keywords = st.text_input(
                "Key fields",
                placeholder="warranty, seller, contract number",
                help="Enter only the fields you want to extract. Do not enter extra fields.",
            )
            template_pdf = st.file_uploader(
                "Template PDF (optional)",
                type=["pdf"],
                key="template_management_add_pdf",
            )
            submitted = st.form_submit_button(
                "Add Template",
                type="primary",
                use_container_width=True,
            )

        if submitted:
            template_name = template_name.strip()
            keyword_list = _format_key_fields(keywords)
            if not template_name:
                st.error("Enter a template name.")
            else:
                pdf_name = ""
                pdf_path = ""
                if template_pdf:
                    pdf_name = template_pdf.name
                    pdf_path = f"{uuid.uuid4().hex}_{template_pdf.name}"
                    Path(files_dir).joinpath(pdf_path).write_bytes(
                        template_pdf.getvalue()
                    )

                if not keyword_list:
                    st.error(
                        "Enter the key fields that should be extracted from matching PDFs."
                    )
                    return

                try:
                    if not inspector.has_table(table_name):
                        with engine.begin() as connection:
                            connection.execute(
                                text(
                                    "CREATE TABLE [Template_master] ("
                                    "[Template_ID] int IDENTITY(1,1) PRIMARY KEY, "
                                    "[Template_Name] nvarchar(500) NOT NULL, "
                                    "[Keywords] nvarchar(max) NOT NULL, "
                                    "[PDF_File_Name] nvarchar(500) NULL, "
                                    "[PDF_File_Path] nvarchar(500) NULL"
                                    ")"
                                )
                            )
                        inspector = inspect(engine)

                    columns = [
                        column["name"]
                        for column in inspector.get_columns(table_name)
                    ]
                    lookup = {
                        re.sub(r"[^a-z0-9]+", "", column.lower()).strip(): column
                        for column in columns
                    }
                    values = {}
                    for normalized, value in {
                        "templatename": template_name,
                        "template": template_name,
                        "name": template_name,
                        "keywords": ", ".join(keyword_list),
                        "keyword": ", ".join(keyword_list),
                        "pdffilename": pdf_name,
                        "filename": pdf_name,
                        "pdffilepath": pdf_path,
                        "filepath": pdf_path,
                    }.items():
                        if normalized in lookup:
                            values[lookup[normalized]] = value

                    if not values:
                        raise LookupError(
                            "Template_master has no recognized template or keyword columns."
                        )

                    pd.DataFrame([values]).to_sql(
                        table_name,
                        con=engine,
                        if_exists="append",
                        index=False,
                    )
                    st.success(f"Template '{template_name}' added successfully.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not add template: {exc}")

    with edit_tab:
        try:
            if not inspector.has_table(table_name):
                st.info("No templates have been added.")
                return

            with engine.connect() as connection:
                templates = pd.read_sql(
                    text(f"SELECT * FROM [{table_name}]"),
                    connection,
                )

            if templates.empty:
                st.info("No templates have been added.")
                return

            lookup = _column_lookup(templates)
            name_column = (
                lookup.get("templatename")
                or lookup.get("template")
                or lookup.get("name")
            )
            keyword_column = lookup.get("keywords") or lookup.get("keyword")
            pdf_name_column = (
                lookup.get("pdffilename") or lookup.get("filename")
            )
            pdf_path_column = (
                lookup.get("pdffilepath") or lookup.get("filepath")
            )
            id_column = lookup.get("templateid")

            if not name_column or not keyword_column:
                st.error(
                    "Template_master must contain a template name and keyword column."
                )
                return

            st.dataframe(
                templates,
                use_container_width=True,
                hide_index=True,
            )

            options = {
                (str(row[id_column]) if id_column else str(index)): row
                for index, row in templates.iterrows()
            }
            selected_key = st.selectbox(
                "Select template to edit",
                options=list(options.keys()),
                format_func=lambda key: str(options[key][name_column]),
            )
            selected = options[selected_key]

            with st.form("template_edit_page_form"):
                edited_name = st.text_input(
                    "Template name",
                    value=str(selected[name_column] or ""),
                )
                edited_keywords = st.text_input(
                    "Key fields",
                    value=str(selected[keyword_column] or ""),
                )
                edited_pdf = st.file_uploader(
                    "Replace template PDF (optional)",
                    type=["pdf"],
                    key=f"template_management_edit_pdf_{selected_key}",
                )
                updated = st.form_submit_button(
                    "Update Template",
                    type="primary",
                    use_container_width=True,
                )

            if updated:
                edited_name = edited_name.strip()
                edited_keyword_list = _format_key_fields(edited_keywords)
                edited_keywords = ", ".join(edited_keyword_list)
                if not edited_name or not edited_keywords:
                    st.error("Template name and keywords are required.")
                else:
                    pdf_name = (
                        str(selected[pdf_name_column] or "")
                        if pdf_name_column else ""
                    )
                    pdf_path = (
                        str(selected[pdf_path_column] or "")
                        if pdf_path_column else ""
                    )
                    if edited_pdf:
                        pdf_name = edited_pdf.name
                        pdf_path = f"{uuid.uuid4().hex}_{edited_pdf.name}"
                        Path(files_dir).joinpath(pdf_path).write_bytes(
                            edited_pdf.getvalue()
                        )

                    update_columns = {
                        name_column: edited_name,
                        keyword_column: edited_keywords,
                    }
                    if pdf_name_column:
                        update_columns[pdf_name_column] = pdf_name
                    if pdf_path_column:
                        update_columns[pdf_path_column] = pdf_path

                    clauses = [
                        f"[{column.replace(']', ']]')}] = :value_{index}"
                        for index, column in enumerate(update_columns)
                    ]
                    parameters = {
                        f"value_{index}": value
                        for index, value in enumerate(update_columns.values())
                    }

                    if id_column and str(selected[id_column]) != "":
                        where_clause = (
                            f"[{id_column.replace(']', ']]')}] = :template_id"
                        )
                        parameters["template_id"] = selected[id_column]
                    else:
                        where_clause = (
                            f"[{name_column.replace(']', ']]')}] = :original_name"
                        )
                        parameters["original_name"] = str(selected[name_column])

                    with engine.begin() as connection:
                        connection.execute(
                            text(
                                f"UPDATE [{table_name}] SET "
                                f"{', '.join(clauses)} WHERE {where_clause}"
                            ),
                            parameters,
                        )
                    st.success(f"Template '{edited_name}' updated successfully.")
                    st.rerun()
        except Exception as exc:
            st.error(f"Could not load templates: {exc}")
