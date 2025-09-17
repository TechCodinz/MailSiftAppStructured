# gui.py
import threading
import time
import importlib
import webbrowser

# robust import that works inside PyInstaller one-file bundles
flask_app = importlib.import_module("app").app

def start_server() -> None:
    # don't use debug=True inside a packaged app
    flask_app.run(host="127.0.0.1", port=5000, debug=False)


def try_create_webview(url: str) -> bool:
    try:
        import webview
    except Exception as e:
        print('pywebview not available or failed to import:', e)
        return False

    try:
        webview.create_window("MailSift - Email Extractor", url, width=1000, height=700)
        webview.start()
        return True
    except Exception as e:
        print('pywebview failed to create window:', e)
        return False


if __name__ == "__main__":
    url = "http://127.0.0.1:5000"
    t = threading.Thread(target=start_server, daemon=True)
    t.start()

    # give server a moment to start
    time.sleep(0.6)

    opened = try_create_webview(url)
    if not opened:
        print('Falling back to opening default web browser at', url)
        webbrowser.open(url)
