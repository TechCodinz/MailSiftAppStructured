# Building the MailSift Desktop App (Linux)

Prerequisites
- Python 3.11+ (3.13 OK)
- pip install: pyinstaller, pywebview
- Optional: system packages for OCR/PDF if you want those features offline

Steps
1) Install dependencies:
```
pip install -r requirements.txt
pip install pyinstaller pywebview
```

2) Build with PyInstaller:
```
pyinstaller gui.spec
```
This will produce `dist/gui` (binary) and supporting files.

3) Run the desktop app:
```
./dist/gui
```
It starts a local server on 127.0.0.1:5000 and opens a native window via pywebview. If webview fails, it opens your default browser.

4) Configure environment
Create a `.env` or export env vars before launching to set:
- MAILSIFT_SECRET, MAILSIFT_ADMIN_KEY
- PRICE_USDT, FREE_SCRAPE_LIMIT, PREVIEW_SAMPLE_SIZE
- MAILSIFT_RECEIVE_ADDRESS (TRC20), WALLET_BTC, WALLET_ETH
- SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_FROM

5) Distributing
- Zip contents of `dist/` or produce a one‑file binary (adjust `gui.spec` to onefile if preferred).
- For auto‑update or per‑OS builds, set up CI to build for Windows/macOS via GitHub Actions with OS-specific webview backends.

Serving the binary from the server
Place the built artifact(s) in `dist/` and the web app route `/download/desktop` will serve the latest build.

CI builds and GitHub Release
- On tag push `v*`, GitHub Actions builds desktop binaries for Linux, Windows, and macOS and uploads them to the Release.
- Workflow file: `.github/workflows/desktop-release.yml`
- Manual run supported via “Run workflow”.