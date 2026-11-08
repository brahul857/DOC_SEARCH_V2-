# PDF Keyword & Value Finder

A small Python + Streamlit project that lets you upload **one PDF at a time**, search for any keyword, view matching context with page numbers, and export results to CSV.

## Features

- Single-PDF uploader (a new upload replaces the current file)
- Extract text page by page using PyMuPDF
- Case-insensitive keyword search
- Context snippets around each match
- Best-effort extraction of common fields such as contract number, seller, brand, quantity, price, warranty, and dates
- CSV download for keyword matches and detected fields
- Full extracted text preview by page

## Run on Windows

1. Install Python 3.10 or newer.
2. Extract this ZIP.
3. Open Command Prompt in the project folder.
4. Create and activate a virtual environment:

```bat
python -m venv .venv
.venv\Scripts\activate
```

5. Install packages:

```bat
pip install -r requirements.txt
```

6. Start the application:

```bat
streamlit run app.py
```

Alternatively, use `uv` from the repository root:

```bat
uv run streamlit run pdf_keyword_search/app.py
```

The browser will open the local Streamlit app. Upload a PDF using **Upload one PDF file**.

## Example keywords for the supplied GeM contract PDFs

Try searching these terms in the relevant PDF:

- `GEMC-511687721084070` — service contract number
- `Aegis Solutions` — seller / service provider
- `Canon` — brand or product
- `12990` — service contract value
- `GEMC-511687767103071` — product contract number
- `305854.412` — product order value
- `warranty` — warranty details

The values above are examples from the two GeM PDFs discussed when this project was requested. Upload either PDF individually to search it; this app does not load both at once.

## Important notes

- Automatic field extraction uses label patterns and is intentionally best-effort because PDF layouts differ. Keyword search and the page text preview let you verify the original wording.
- Scanned PDFs may contain images rather than selectable text. OCR is not included in this starter version.
- The application processes the uploaded file locally in the Streamlit session; it does not send the PDF to an external API.
