import os
import json
import hmac
import hashlib
import time
from typing import Optional, Any, Dict
import smtplib
from email.message import EmailMessage
import requests
import tempfile
import fcntl
import sqlite3

PAYMENTS_FILE = os.path.join(os.path.dirname(__file__), 'payments.json')
SQLITE_DB = os.environ.get('MAILSIFT_SQLITE_DB')
SECRET = os.environ.get('MAILSIFT_SECRET', 'dev-secret-key')
ADMIN_KEY = os.environ.get('MAILSIFT_ADMIN_KEY', 'admin-secret')


def _load_payments() -> Dict[str, Dict[str, Any]]:
    if SQLITE_DB:
        try:
            with sqlite3.connect(SQLITE_DB) as conn:
                conn.execute(
                    'CREATE TABLE IF NOT EXISTS payments ('
                    'txid TEXT PRIMARY KEY,'
                    'address TEXT,'
                    'amount REAL,'
                    'timestamp INTEGER,'
                    'verified INTEGER,'
                    'license TEXT,'
                    'contact TEXT,'
                    'verified_by TEXT,'
                    'verified_at INTEGER,'
                    'asset TEXT)'
                )
                rows = conn.execute(
                    'SELECT '
                    'txid,address,amount,timestamp,verified,license,'
                    'contact,verified_by,verified_at,asset '
                    'FROM payments'
                ).fetchall()
                out = {}
                for r in rows:
                    out[r[0]] = {
                        'txid': r[0],
                        'address': r[1],
                        'amount': r[2],
                        'timestamp': r[3],
                        'verified': bool(r[4]),
                        'license': r[5],
                        'contact': r[6],
                        'verified_by': r[7],
                        'verified_at': r[8],
                        'asset': r[9],
                    }
                return out
        except Exception:
            pass
    if not os.path.exists(PAYMENTS_FILE):
        return {}
    try:
        with open(PAYMENTS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_payments(data: Dict[str, Dict[str, Any]]) -> None:
    if SQLITE_DB:
        try:
            with sqlite3.connect(SQLITE_DB) as conn:
                conn.execute(
                    'CREATE TABLE IF NOT EXISTS payments ('
                    'txid TEXT PRIMARY KEY,'
                    'address TEXT,'
                    'amount REAL,'
                    'timestamp INTEGER,'
                    'verified INTEGER,'
                    'license TEXT,'
                    'contact TEXT,'
                    'verified_by TEXT,'
                    'verified_at INTEGER,'
                    'asset TEXT)'
                )
                for txid, v in data.items():
                    conn.execute(
                        'INSERT OR REPLACE INTO payments ('
                        'txid,address,amount,timestamp,verified,license,contact,verified_by,verified_at,asset'
                        ') VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (
                            txid,
                            v.get('address'),
                            float(v.get('amount') or 0.0),
                            int(v.get('timestamp') or 0),
                            1 if v.get('verified') else 0,
                            v.get('license'),
                            v.get('contact'),
                            v.get('verified_by'),
                            v.get('verified_at'),
                            v.get('asset'),
                        ),
                    )
                conn.commit()
            return
        except Exception:
            pass
    # Atomic write with file lock and audit shadow
    dirpath = os.path.dirname(PAYMENTS_FILE)
    os.makedirs(dirpath, exist_ok=True)
    temp_fd, temp_path = tempfile.mkstemp(
        dir=dirpath, prefix='payments.', suffix='.tmp'
    )
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
                af.write(
                    json.dumps({
                        'ts': int(time.time()),
                        'event': 'save',
                        'count': len(data),
                    }) + '\n'
                )
        except Exception:
            pass
    finally:
        try:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        except Exception:
            pass


def record_payment(txid: str, address: str, amount: float = 0.0) -> Dict[str, Any]:
    data = _load_payments()
    now = int(time.time())
    if txid in data:
        return data[txid]
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


def mark_verified(txid: str, verifier: Optional[str] = None) -> Optional[Dict[str, Any]]:
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


def get_payment(txid: str) -> Optional[Dict[str, Any]]:
    data = _load_payments()
    return data.get(txid)


def list_payments() -> Dict[str, Dict[str, Any]]:
    # If JSON exists and SQLite enabled but empty, migrate once
    data = _load_payments()
    try:
        if SQLITE_DB and os.path.exists(PAYMENTS_FILE):
            # If DB has fewer rows than JSON, populate
            with sqlite3.connect(SQLITE_DB) as conn:
                conn.execute(
                    'CREATE TABLE IF NOT EXISTS payments ('
                    'txid TEXT PRIMARY KEY,'
                    'address TEXT,'
                    'amount REAL,'
                    'timestamp INTEGER,'
                    'verified INTEGER,'
                    'license TEXT,'
                    'contact TEXT,'
                    'verified_by TEXT,'
                    'verified_at INTEGER,'
                    'asset TEXT)'
                )
                row = conn.execute('SELECT COUNT(1) FROM payments').fetchone()
                count = int(row[0] or 0)
                if count < len(data):
                    _save_payments(data)
    except Exception:
        pass
    return data


def generate_license_for(txid: str) -> str:
    # HMAC-SHA256 of txid + timestamp
    ts = str(int(time.time()))
    mac = hmac.new(
        SECRET.encode('utf-8'),
        f"{txid}:{ts}".encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    return f"LS-{ts}-{mac[:24]}"


def verify_admin_key(key: str) -> bool:
    # read env at call time to respect monkeypatching in tests
    expected = os.environ.get('MAILSIFT_ADMIN_KEY', ADMIN_KEY)
    return key == expected


def verify_trc20_tx_online(txid: str) -> bool:  # noqa: C901
    """Attempt to verify a TRC20/USDT transaction using public TronGrid endpoints.

    Best-effort: try a public API and look for the txid. If requests is not available
    or the API fails, return False so the admin can verify manually.
    """
    # requests is imported at module-level to allow tests to monkeypatch payments.requests
    if 'requests' not in globals():
        try:
            import requests as _r
            globals()['requests'] = _r
        except Exception:
            return False

    # TronGrid public API (prefer v1). If TRONGRID_KEY provided, use it.
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


def send_license_email(to_address: str, license_key: str) -> bool:
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
    msg.set_content(
        f"Thank you for your payment. Your license key: {license_key}\n\nKeep it safe."
    )
    try:
        with smtplib.SMTP(host, port, timeout=10) as s:
            if user and pwd:
                s.starttls()
                s.login(user, pwd)
            s.send_message(msg)
        return True
    except Exception:
        return False
