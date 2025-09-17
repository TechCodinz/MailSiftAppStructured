import os
import json
import hmac
import hashlib
import time
from typing import Optional
import smtplib
from email.message import EmailMessage
import requests
import tempfile
import fcntl

PAYMENTS_FILE = os.path.join(os.path.dirname(__file__), 'payments.json')
SECRET = os.environ.get('MAILSIFT_SECRET', 'dev-secret-key')
ADMIN_KEY = os.environ.get('MAILSIFT_ADMIN_KEY', 'admin-secret')


def _load_payments():
    if not os.path.exists(PAYMENTS_FILE):
        return {}
    try:
        with open(PAYMENTS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_payments(data):
    # Atomic write with file lock and audit shadow
    dirpath = os.path.dirname(PAYMENTS_FILE)
    os.makedirs(dirpath, exist_ok=True)
    temp_fd, temp_path = tempfile.mkstemp(dir=dirpath, prefix='payments.', suffix='.tmp')
    try:
        with os.fdopen(temp_fd, 'w', encoding='utf-8') as tf:
            json.dump(data, tf, indent=2)
            tf.flush()
            os.fsync(tf.fileno())
        # Acquire lock on target during replace
        with open(PAYMENTS_FILE, 'a+', encoding='utf-8') as target:
            try:
                fcntl.flock(target.fileno(), fcntl.LOCK_EX)
            except Exception:
                pass
            os.replace(temp_path, PAYMENTS_FILE)
            try:
                fcntl.flock(target.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
        # Append audit line
        try:
            audit_path = os.path.join(dirpath, 'payments_audit.log')
            with open(audit_path, 'a', encoding='utf-8') as af:
                af.write(json.dumps({'ts': int(time.time()), 'event': 'save', 'count': len(data)}) + '\n')
        except Exception:
            pass
    finally:
        try:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        except Exception:
            pass


def record_payment(txid: str, address: str, amount: float = 0.0):
    data = _load_payments()
    now = int(time.time())
    data[txid] = {
        'txid': txid,
        'address': address,
        'amount': amount,
        'timestamp': now,
        'verified': False,
        'license': None,
    }
    _save_payments(data)
    return data[txid]


def mark_verified(txid: str, verifier: Optional[str] = None):
    data = _load_payments()
    if txid not in data:
        return None
    data[txid]['verified'] = True
    data[txid]['verified_by'] = verifier
    data[txid]['verified_at'] = int(time.time())
    # generate license
    license_key = generate_license_for(txid)
    data[txid]['license'] = license_key
    _save_payments(data)
    # attempt to email license to contact if provided
    contact = data[txid].get('contact') or data[txid].get('address')
    try:
        if contact and '@' in contact:
            send_license_email(contact, license_key)
    except Exception:
        pass
    return data[txid]


def get_payment(txid: str):
    data = _load_payments()
    return data.get(txid)


def list_payments():
    return _load_payments()


def generate_license_for(txid: str) -> str:
    # HMAC-SHA256 of txid + timestamp
    ts = str(int(time.time()))
    mac = hmac.new(SECRET.encode('utf-8'), f"{txid}:{ts}".encode('utf-8'), hashlib.sha256).hexdigest()
    return f"LS-{ts}-{mac[:24]}"


def verify_admin_key(key: str) -> bool:
    # read env at call time to respect monkeypatching in tests
    expected = os.environ.get('MAILSIFT_ADMIN_KEY', ADMIN_KEY)
    return key == expected


def verify_trc20_tx_online(txid: str) -> bool:
    """Attempt to verify a TRC20/USDT transaction using public TronGrid endpoints when possible.

    This is best-effort: we'll try to call a public API and look for the txid. If requests is
    not available or the API fails, return False so the admin can verify manually.
    """
    # requests is imported at module-level to allow tests to monkeypatch payments.requests
    if 'requests' not in globals():
        try:
            import requests as _r
            globals()['requests'] = _r
        except Exception:
            return False

    # TronGrid public API (prefer v1). If TRONGRID_KEY is provided, use it in headers.
    base = os.environ.get('TRONGRID_BASE', 'https://api.trongrid.io')
    api_key = os.environ.get('TRONGRID_KEY')
    headers = {'Accept': 'application/json'}
    if api_key:
        headers['TRON-PRO-API-KEY'] = api_key

    endpoints = [
        f"{base}/v1/transactions/{txid}",
        f"{base}/wallet/gettransactionbyid?value={txid}",
    ]

    expected_address = os.environ.get('MAILSIFT_RECEIVE_ADDRESS')

    for url in endpoints:
        try:
            r = requests.get(url, timeout=6, headers=headers)
            if r.status_code != 200:
                continue
            try:
                j = r.json()
            except Exception:
                # non-json response
                body = r.text.lower() if hasattr(r, 'text') else ''
                if txid.lower() in body:
                    return True
                continue

            # Flatten JSON to text and search for txid and optional expected address
            payload_text = json.dumps(j).lower()
            if txid.lower() not in payload_text:
                continue
            if expected_address:
                if expected_address.lower() in payload_text:
                    return True
                # address not found in this response, keep checking other endpoints
                continue
            return True
        except Exception:
            continue
    return False


def send_license_email(to_address: str, license_key: str):
    """Send a simple license email; uses SMTP_* env vars if present."""
    host = os.environ.get('SMTP_HOST')
    port = int(os.environ.get('SMTP_PORT', '0') or 0)
    user = os.environ.get('SMTP_USER')
    pwd = os.environ.get('SMTP_PASS')
    sender = os.environ.get('EMAIL_FROM', 'no-reply@example.com')

    if not host or port == 0:
        return False
    msg = EmailMessage()
    msg['Subject'] = 'Your MailSift License Key'
    msg['From'] = sender
    msg['To'] = to_address
    msg.set_content(f"Thank you for your payment. Your license key: {license_key}\n\nKeep it safe.")
    try:
        with smtplib.SMTP(host, port, timeout=10) as s:
            if user and pwd:
                s.starttls()
                s.login(user, pwd)
            s.send_message(msg)
        return True
    except Exception:
        return False
