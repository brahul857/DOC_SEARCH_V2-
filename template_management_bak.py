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


def _add_pending_key_field():
    version = st.session_state.get("template_management_form_version", 0)
    input_key = f"template_management_key_field_input_{version}"
    pending = st.session_state.setdefault(
        "template_management_pending_key_fields", []
    )
    existing = {field.lower() for field in pending}
    for field in _format_key_fields(st.session_state.get(input_key, "")):
        if field.lower() not in existing:
            pending.append(field)
            existing.add(field.lower())
    st.session_state[input_key] = ""  # allowed: we're inside the widget's own callback

def _remove_pending_key_field(index):
    pending = st.session_state.get("template_management_pending_key_fields", [])
    if 0 <= index < len(pending):
        pending.pop(index)

BUBBLE_CSS = """
<style>
div[class*="st-key-template_management_remove_"] button {
    border-radius: 999px;
    background: rgba(26, 115, 232, 0.12);
    border: 1px solid rgba(26, 115, 232, 0.45);
    color: #4c8df6;
    font-weight: 500;
    min-height: 0;
    padding: 0.2rem 1rem;
    white-space: nowrap;
}
div[class*="st-key-template_management_remove_"] button:hover {
    background: rgba(26, 115, 232, 0.25);
    border-color: #1a73e8;
    color: #1a73e8;
}
.tm-bubble {
    display: inline-block;
    border-radius: 999px;
    background: rgba(26, 115, 232, 0.12);
    border: 1px solid rgba(26, 115, 232, 0.45);
    color: #4c8df6;
    font-weight: 500;
    padding: 0.2rem 1rem;
    margin: 0 0.4rem 0.4rem 0;
}
</style>
"""
def _display_key_field_boxes(fields, removable=False, per_row=4):
    """Show key fields as rounded bubbles. Removable bubbles are buttons:
    click a bubble to remove it."""
    st.markdown(BUBBLE_CSS, unsafe_allow_html=True)
    if not removable:
        st.markdown(
            "".join(
                f'<span class="tm-bubble">{html.escape(f)}</span>' for f in fields
            ),
            unsafe_allow_html=True,
        )
        return
    for start in range(0, len(fields), per_row):
        columns = st.columns(per_row)
        for offset, (column, field) in enumerate(
            zip(columns, fields[start:start + per_row])
        ):
            index = start + offset
            column.button(
                f"{field}  ✕",
                key=f"template_management_remove_{index}",
                on_click=_remove_pending_key_field,
                args=(index,),
                help="Click to remove",
            )


def _save_template(engine, files_dir, table_name):
    ss = st.session_state
    version = ss["template_management_form_version"]
    name = str(ss.get(f"template_management_add_name_{version}", "")).strip()
    pdf = ss.get(f"template_management_add_pdf_{version}")
    keyword_list = list(ss.get("template_management_pending_key_fields", []))

    ss.pop("template_management_add_error", None)
    ss.pop("template_management_add_success_message", None)

    if not name:
        ss["template_management_add_error"] = "Enter a template name."
        return
    if not keyword_list:
        ss["template_management_add_error"] = (
            "Add at least one key field by entering it and pressing Enter."
        )
        return

    try:
        pdf_name, pdf_path = "", ""
        if pdf:
            pdf_name = pdf.name
            pdf_path = f"{uuid.uuid4().hex}_{pdf.name}"
            Path(files_dir).joinpath(pdf_path).write_bytes(pdf.getvalue())

        if not inspect(engine).has_table(table_name):
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

        columns = [c["name"] for c in inspect(engine).get_columns(table_name)]
        lookup = {
            re.sub(r"[^a-z0-9]+", "", c.lower()).strip(): c for c in columns
        }
        values = {}
        for normalized, value in {
            "templatename": name,
            "template": name,
            "name": name,
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
            table_name, con=engine, if_exists="append", index=False
        )
    except Exception as exc:
        ss["template_management_add_error"] = f"Could not add template: {exc}"
        return

    # Success: clear everything (new version = brand-new empty widgets)
    ss["template_management_add_success_message"] = (
        f"Template '{name}' added successfully."
    )
    ss["template_management_pending_key_fields"] = []
    ss["template_management_form_version"] = version + 1
    
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
        st.session_state.setdefault("template_management_pending_key_fields", [])
        st.session_state.setdefault("template_management_form_version", 0)
        version = st.session_state["template_management_form_version"]

        template_name = st.text_input(
            "Template name",
            placeholder="e.g. GeM Contract",
            key=f"template_management_add_name_{version}",
        )
        st.text_input(
            "Key field",
            placeholder="Enter one field and press Enter",
            help="Press Enter after each field to add it to the list.",
            key=f"template_management_key_field_input_{version}",
            on_change=_add_pending_key_field,
        )

        # Temporary list, shown right below the text box
        pending_key_fields = st.session_state["template_management_pending_key_fields"]
        if pending_key_fields:
                st.markdown("**Key fields to save (not in database yet):**")
                _display_key_field_boxes(pending_key_fields, removable=True)
            
                add_error = st.session_state.pop("template_management_add_error", None)
                if add_error:
                    st.error(add_error)
                add_success_message = st.session_state.pop(
                    "template_management_add_success_message", None
                )
                if add_success_message:
                    st.success(add_success_message)

                st.file_uploader(
                    "Template PDF (optional)",
                    type=["pdf"],
                    key=f"template_management_add_pdf_{version}",
                )
                st.button(
                    "Add Template",
                    type="primary",
                    use_container_width=True,
                    key="template_management_add_button",
                    on_click=_save_template,
                    args=(engine, files_dir, table_name),
                )
            
                saved = False
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
                    saved = True
                except Exception as exc:
                    st.error(f"Could not add template: {exc}")

                # Outside try/except, at the same indent as "saved = False"
                if saved:
                    st.session_state["template_management_add_success_message"] = (
                        f"Template '{template_name}' added successfully."
                    )
                    st.session_state["template_management_pending_key_fields"] = []
                    st.session_state["template_management_form_version"] += 1
                    st.rerun()
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
