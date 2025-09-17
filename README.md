# MailSift

MailSift is a small utility that extracts emails from text, HTML, and uploaded files and provides a minimal Flask UI for demoing extraction and downloads.

Quick start

1. Create and activate a venv

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install deps

```powershell
pip install -r requirements.txt
```

3. Run tests

```powershell
python -m pytest -q
```

4. Run the app

```powershell
python app.py
```

Notes
- The project includes optional dependencies for PDF, DOCX, image OCR and other parsing. The core extraction functions are pure-Python and tested.
MailSift - Local Email Extractor

Quick start (Windows PowerShell):

1. Create and activate a Python virtualenv (optional but recommended):

   python -m venv .venv; .\.venv\Scripts\Activate.ps1

2. Install requirements:

   pip install -r requirements.txt

3. Run the app (GUI wrapper will start a local webview):

   python gui.py

What I changed
- Fixed `gui.py` stray text.
- Implemented a working `app.py` that extracts emails from pasted text and .txt/.csv files, categorizes them, and allows CSV download.
- Added `requirements.txt` and this `README.md`.

Crypto payment flow (manual TXID verification)
- The app supports manual crypto payments (e.g., USDT TRC20). Users can submit the TXID on the paywall page after paying.
- Submitted payments are stored in `payments.json` and can be reviewed at `/admin/payments` (this endpoint is not protected in this demo — add auth for production).


- `MAILSIFT_SECRET` : HMAC secret used to sign license tokens (default included; set a strong secret in production).
- `ADMIN_USER` and `ADMIN_PASSWORD` : if set, protect `/admin/payments` with basic auth.
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `EMAIL_FROM` : if provided, the server will attempt to email generated license keys to the payer contact address.
Notes and next steps
Notes and next steps
- Current file parsing supports TXT/CSV, DOCX (python-docx), PDF (pdfminer.six) and OCR fallbacks (pytesseract + pdf2image). For OCR you'll need:

- Tesseract OCR installed and on PATH: https://github.com/tesseract-ocr/tesseract
- Poppler (for pdf2image) installed and on PATH (Windows: add bin directory to PATH).

For automatic TRC20 verification set environment variables:

- `TRONGRID_KEY` (optional) — TronGrid API key for more reliable queries.
- `MAILSIFT_RECEIVE_ADDRESS` — when set, verification will check the on-chain recipient address.

Run the server:

```powershell
pip install -r requirements.txt
python server.py
```
