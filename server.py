from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    jsonify,
    send_file,
)
import os
from app import (
    extract_emails_from_text,
    extract_emails_from_html,
    group_by_provider,
    session_increment_scrape_quota,
    detect_provider,
    extract_domain,
    classify_expertise,
    enrich_meta_for_emails,
)
from file_parsing import extract_text_from_file
from payments import (
    record_payment,
    mark_verified,
    list_payments,
    verify_admin_key,
    verify_trc20_tx_online,
)
from functools import wraps
import io
import csv
import json
import time
from datetime import datetime
import logging
import uuid
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from typing import Callable
from flask.wrappers import Response

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
except Exception:
    Limiter = None
    def get_remote_address() -> str:
        return request.remote_addr or ""

try:
    import sentry_sdk
    from sentry_sdk.integrations.flask import FlaskIntegration
except Exception:
    sentry_sdk = None

app = Flask(__name__)
app.secret_key = os.environ.get('MAILSIFT_SECRET', 'dev-secret-key')

# Basic production-hardening defaults (can be overridden via env FLASK_*)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = (
    os.environ.get('SESSION_COOKIE_SECURE', 'true').lower()
    in ('1', 'true', 'yes')
)
app.config['MAX_CONTENT_LENGTH'] = (
    int(os.environ.get('MAX_CONTENT_LENGTH_MB', '10')) * 1024 * 1024
)

# Initialize logging
logging.basicConfig(level=os.environ.get('LOG_LEVEL', 'INFO'))
logger = logging.getLogger('mailsift')

# Startup security checks
if os.environ.get('ENVIRONMENT', 'production').lower() == 'production':
    if app.secret_key in ('dev-secret-key', '', None):
        raise RuntimeError('MAILSIFT_SECRET must be set in production')
    if (
        (os.environ.get('MAILSIFT_ADMIN_KEY') or 'admin-secret')
        in ('admin-secret', 'change-me-admin')
    ):
        raise RuntimeError(
            'MAILSIFT_ADMIN_KEY must be set to a strong value in production'
        )

# Request ID injection
@app.before_request
def add_request_id() -> None:
    rid = request.headers.get('X-Request-ID') or str(uuid.uuid4())
    request.request_id = rid
    request._start_ts = time.time()

@app.after_request
def add_request_id_header(resp: Response) -> Response:
    if hasattr(request, 'request_id'):
        resp.headers['X-Request-ID'] = request.request_id
    try:
        dur = (time.time() - getattr(request, '_start_ts', time.time())) * 1000.0
        logger.info(
            '%s %s %s %0.1fms id=%s ip=%s',
            request.method,
            request.path,
            resp.status_code,
            dur,
            request.request_id,
            request.headers.get('X-Forwarded-For', request.remote_addr),
        )
        HTTP_REQUESTS.labels(request.method, request.path, resp.status_code).inc()
        HTTP_LATENCY.labels(request.method, request.path).observe(dur / 1000.0)
    except Exception:
        pass
    return resp

# Sentry
if sentry_sdk and os.environ.get('SENTRY_DSN'):
    sentry_sdk.init(
        dsn=os.environ['SENTRY_DSN'],
        integrations=[FlaskIntegration()],
        traces_sample_rate=float(
            os.environ.get('SENTRY_TRACES', '0.0') or 0.0
        ),
    )

# Limiter
if Limiter:
    storage_uri = os.environ.get('RATELIMIT_STORAGE_URI')
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=[os.environ.get('GLOBAL_RATE_LIMIT', '120 per minute')],
        storage_uri=storage_uri,
    )
else:
    limiter = None

HTTP_REQUESTS = Counter(
    'mailsift_http_requests_total',
    'HTTP requests',
    ['method', 'path', 'status'],
)
HTTP_LATENCY = Histogram(
    'mailsift_http_request_duration_seconds',
    'HTTP request latency',
    ['method', 'path'],
)


@app.after_request
def add_security_headers(resp: Response) -> Response:
    # Minimal safe headers; adjust CSP as needed if you add external scripts
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'DENY')
    resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    # Allow inline styles/scripts used in templates
    csp = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "script-src 'self' 'unsafe-inline'"
    )
    resp.headers.setdefault('Content-Security-Policy', csp)
    if request.scheme == 'https':
        resp.headers.setdefault(
            'Strict-Transport-Security',
            'max-age=63072000; includeSubDomains; preload',
        )
    return resp


def admin_auth_required(f: Callable[..., Response]) -> Callable[..., Response]:
    @wraps(f)
    def inner(*args: object, **kwargs: object) -> Response:
        # IP allowlist (optional)
        allowed = (os.environ.get('ADMIN_IP_ALLOWLIST') or '').strip()
        if allowed:
            ip = (
                request.headers.get('X-Forwarded-For', request.remote_addr or '')
                .split(',')[0]
                .strip()
            )
            allow = {x.strip() for x in allowed.split(',') if x.strip()}
            if ip not in allow:
                return jsonify({'error': 'forbidden'}), 403
        # Support either simple key header or HTTP basic auth
        key = (
            request.args.get('key')
            or request.form.get('key')
            or request.headers.get('X-Admin-Key')
        )
        if not key:
            # try basic auth
            auth = request.authorization
            if auth and auth.password:
                key = auth.password
        if not verify_admin_key(key or ''):
            return jsonify({'error': 'unauthorized'}), 401
        return f(*args, **kwargs)
    return inner


@app.route('/')
def index() -> Response | str:
    # show any current session results
    results = None
    if 'extracted' in session:
        extracted = session.get('extracted', [])
        meta = session.get('meta', {})
        results = {
            'valid': group_by_provider(extracted),
            'meta': meta,
            'invalid': session.get('invalid', []),
        }
    return render_template('index.html', results=results)


def get_wallets() -> dict:
    return {
        'btc': os.environ.get('MAILSIFT_WALLET_BTC'),
        'trc20': (
            os.environ.get('MAILSIFT_WALLET_TRC20')
            or os.environ.get('MAILSIFT_RECEIVE_ADDRESS')
        ),
        'eth': os.environ.get('MAILSIFT_WALLET_ETH'),
    }


@app.route('/paywall')
def paywall() -> Response | str:
    return render_template('paywall.html', wallets=get_wallets())


@app.route('/scrape', methods=['POST'])
@limiter.limit(os.environ.get('SCRAPE_RATE_LIMIT', '20/minute') if limiter else '20/minute')
def scrape() -> Response | str:  # noqa: C901
    # Enforce free quota limit unless unlocked
    free_limit = int(os.environ.get('FREE_SCRAPE_QUOTA', '3') or 3)
    if (
        not session.get('unlocked')
        and session.get('scrape_quota', 0) >= free_limit
    ):
        return render_template(
            'paywall.html',
            error='Free quota reached. Please unlock to continue.',
            wallets=get_wallets(),
        )
    # support text input, file upload, or one/multiple URLs
    text = ''
    if 'text_input' in request.form and request.form['text_input'].strip():
        text = request.form['text_input']
    elif 'file_input' in request.files:
        f = request.files['file_input']
        text = extract_text_from_file(f.stream, f.filename)

    url_raw = request.form.get('url', '').strip()
    url_list = [u.strip() for u in (url_raw or '').splitlines() if u.strip()]
    if not url_list and ',' in url_raw:
        url_list = [u.strip() for u in url_raw.split(',') if u.strip()]

    per_site = {}
    total_valid = []
    total_invalid = []

    # If we have textual input or file text, extract locally
    if text:
        valid, invalid = extract_emails_from_text(text)
        session['extracted'] = sorted(set(session.get('extracted', []) + valid))
        session['invalid'] = sorted(set(session.get('invalid', []) + invalid))
        meta = session.get('meta', {})
        for e in valid:
            if e not in meta:
                meta[e] = {'role': False}
        session['meta'] = meta

    # If we have URLs, fetch them concurrently (best-effort)
    if url_list:
        try:
            import requests
            from concurrent.futures import ThreadPoolExecutor, as_completed

            headers = {'User-Agent': 'MailSift/1.0 (+https://example)'}

            def fetch(url: str) -> tuple[str, list[str], list[str]]:
                try:
                    r = requests.get(url, timeout=8, headers=headers)
                    html = r.text
                    v, iv = extract_emails_from_html(html)
                    return url, v, iv
                except Exception:
                    return url, [], ['fetch_failed']

            with ThreadPoolExecutor(max_workers=min(8, max(2, len(url_list)))) as ex:
                futures = {ex.submit(fetch, u): u for u in url_list}
                for fut in as_completed(futures):
                    url = futures[fut]
                    try:
                        u, v, iv = fut.result()
                    except Exception:
                        u, v, iv = url, [], ['fetch_failed']
                    # collect per-site results
                    per_site[u] = {'valid': v, 'invalid': iv}
                    total_valid.extend(v)
                    total_invalid.extend(iv)
        except Exception:
            # requests missing or network error; ignore and continue
            for u in url_list:
                per_site[u] = {'error': 'fetch_unavailable'}

    # merge results
    merged = sorted(set(session.get('extracted', []) + total_valid))
    session['extracted'] = merged
    session['invalid'] = sorted(set(session.get('invalid', []) + total_invalid))
    session['meta'] = enrich_meta_for_emails(merged, session.get('meta', {}))

    # Increment quota once per scrape attempt
    session_increment_scrape_quota()

    emails_all = session.get('extracted', [])
    provider_groups = group_by_provider(emails_all)
    # compute domain and expertise maps
    by_domain: dict[str, list[str]] = {}
    by_category: dict[str, list[str]] = {}
    for e in emails_all:
        d = extract_domain(e)
        if d:
            by_domain.setdefault(d, []).append(e)
        cat = classify_expertise(e)
        by_category.setdefault(cat, []).append(e)
    # hero metrics
    total_count = len(emails_all)
    unique_domains = len({
        extract_domain(e)
        for e in emails_all
        if extract_domain(e)
    })
    categories_present = len(by_category)
    results = {
        'valid': provider_groups,
        'per_site': per_site or None,
        'invalid': session.get('invalid', []),
        'meta': session.get('meta', {}),
        'by_domain': by_domain,
        'by_category': by_category,
        'hero': {
            'total_emails': total_count,
            'unique_domains': unique_domains,
            'categories': categories_present,
        }
    }
    return render_template('index.html', results=results)


@app.route('/pay', methods=['POST'])
@limiter.limit(os.environ.get('PAY_RATE_LIMIT', '5/minute') if limiter else '5/minute')
def pay() -> Response | str:
    if request.method == 'POST':
        txid = (request.form.get('txid') or '').strip()
        address = (
            (request.form.get('contact') or request.form.get('address') or '')
            .strip()
        )
        contact = (request.form.get('contact') or '').strip()
        asset = (request.form.get('asset') or '').strip()
        amt_raw = (request.form.get('amount') or '').strip()
        try:
            amount = float(amt_raw)
        except Exception:
            amount = 0.0
        if not txid or not address:
            return render_template(
                'paywall.html',
                error='txid and address required',
                wallets=get_wallets(),
            )
        # minimal txid sanity check and duplicate guard
        if len(txid) < 8 or len(txid) > 128:
            return render_template(
                'paywall.html',
                error='Invalid TXID format',
                wallets=get_wallets(),
            )
        data = list_payments()
        if txid in data:
            return render_template(
                'paywall.html',
                error=(
                    'This TXID is already submitted. '
                    'Await verification or redeem.'
                ),
                wallets=get_wallets(),
            )
        record_payment(txid, address, amount)
        # attach contact if provided
        if txid in data and contact:
            data[txid]['contact'] = contact
            if asset:
                data[txid]['asset'] = asset
            # save back
            from payments import _save_payments
            _save_payments(data)
        error_msg = f'Payment received. Awaiting verification. TXID: {txid}'
        return render_template(
            'paywall.html',
            error=error_msg,
            wallets=get_wallets(),
        )
    return render_template('paywall.html')


@app.route('/redeem', methods=['POST'])
def redeem() -> Response | str:
    key = request.form.get('license') or request.form.get('txid')
    payments = list_payments()
    for txid, info in payments.items():
        if info.get('license') == key or txid == key:
            session['unlocked'] = True
            return render_template(
                'paywall.html',
                error='Unlocked. License applied.',
                wallets=get_wallets(),
            )
    return render_template(
        'paywall.html',
        error='Invalid license or txid',
        wallets=get_wallets(),
    )


@app.route('/unlock', methods=['POST'])
def unlock() -> Response | str:
    # Accept license key from paywall form and unlock if matches an issued license
    key = (request.form.get('license_key') or '').strip()
    payments = list_payments()
    for txid, info in payments.items():
        if info.get('license') == key:
            session['unlocked'] = True
            return render_template(
                'paywall.html',
                error='Unlocked. License applied.',
                wallets=get_wallets(),
            )
    # Dev bypass for local testing
    if key == 'LET-ME-IN-DEV':
        session['unlocked'] = True
        return render_template(
            'paywall.html',
            error='Unlocked (dev).',
            wallets=get_wallets(),
        )
    return render_template(
        'paywall.html',
        error='Invalid license key',
        wallets=get_wallets(),
    )


@app.route('/admin/payments', methods=['GET', 'POST'])
@limiter.limit(os.environ.get('ADMIN_RATE_LIMIT', '60/minute') if limiter else '60/minute')
@admin_auth_required
def admin_payments() -> Response | str:
    # Render the admin payments template with a list of payment records
    payments = list_payments()
    # payments stored as dict keyed by txid -> convert to list for template
    payment_list = list(payments.values()) if isinstance(payments, dict) else payments
    if request.method == 'POST':
        txid = request.form.get('txid')
        if txid:
            ok = mark_verified(txid)
            if ok:
                return redirect(url_for('admin_payments'))
    return render_template('admin_payments.html', payments=payment_list)


@app.route('/download')
def download() -> Response:
    # Gate downloads if past free quota and not unlocked
    free_limit = int(os.environ.get('FREE_SCRAPE_QUOTA', '3') or 3)
    if (
        not session.get('unlocked')
        and session.get('scrape_quota', 0) >= free_limit
    ):
        return render_template(
            'paywall.html',
            error='Please unlock to download results.',
            wallets=get_wallets(),
        )
    emails = session.get('extracted')
    if not emails:
        return redirect(url_for('index'))
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['email'])
    for e in emails:
        cw.writerow([e])
    mem = io.BytesIO(si.getvalue().encode('utf-8'))
    mem.seek(0)
    return send_file(
        mem,
        mimetype='text/csv',
        as_attachment=True,
        download_name='extracted_emails.csv',
    )


@app.route('/download/by-domain.csv')
def download_by_domain() -> Response:
    free_limit = int(os.environ.get('FREE_SCRAPE_QUOTA', '3') or 3)
    if (
        not session.get('unlocked')
        and session.get('scrape_quota', 0) >= free_limit
    ):
        return render_template(
            'paywall.html',
            error='Please unlock to download results.',
            wallets=get_wallets(),
        )
    emails = session.get('extracted') or []
    if not emails:
        return redirect(url_for('index'))
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['domain', 'email'])
    for e in emails:
        cw.writerow([extract_domain(e), e])
    mem = io.BytesIO(si.getvalue().encode('utf-8'))
    mem.seek(0)
    return send_file(
        mem,
        mimetype='text/csv',
        as_attachment=True,
        download_name='emails_by_domain.csv',
    )


@app.route('/download/by-category.csv')
def download_by_category() -> Response:
    free_limit = int(os.environ.get('FREE_SCRAPE_QUOTA', '3') or 3)
    if (
        not session.get('unlocked')
        and session.get('scrape_quota', 0) >= free_limit
    ):
        return render_template(
            'paywall.html',
            error='Please unlock to download results.',
            wallets=get_wallets(),
        )
    emails = session.get('extracted') or []
    if not emails:
        return redirect(url_for('index'))
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['category', 'email'])
    for e in emails:
        cw.writerow([classify_expertise(e), e])
    mem = io.BytesIO(si.getvalue().encode('utf-8'))
    mem.seek(0)
    return send_file(
        mem,
        mimetype='text/csv',
        as_attachment=True,
        download_name='emails_by_category.csv',
    )


@app.route('/download/json')
def download_json() -> Response:
    free_limit = int(os.environ.get('FREE_SCRAPE_QUOTA', '3') or 3)
    if (
        not session.get('unlocked')
        and session.get('scrape_quota', 0) >= free_limit
    ):
        return render_template(
            'paywall.html',
            error='Please unlock to download results.',
            wallets=get_wallets(),
        )
    emails = session.get('extracted')
    meta = session.get('meta', {})
    if not emails:
        return redirect(url_for('index'))
    payload = []
    for e in emails:
        item = {'email': e}
        item.update(meta.get(e, {}))
        payload.append(item)
    mem = io.BytesIO(json.dumps(payload, indent=2).encode('utf-8'))
    mem.seek(0)
    return send_file(
        mem,
        mimetype='application/json',
        as_attachment=True,
        download_name='extracted_emails.json',
    )


@app.route('/download/excel')
def download_excel() -> Response:
    free_limit = int(os.environ.get('FREE_SCRAPE_QUOTA', '3') or 3)
    if (
        not session.get('unlocked')
        and session.get('scrape_quota', 0) >= free_limit
    ):
        return render_template(
            'paywall.html',
            error='Please unlock to download results.',
            wallets=get_wallets(),
        )
    emails = session.get('extracted') or []
    meta = session.get('meta', {})
    if not emails:
        return redirect(url_for('index'))
    try:
        from openpyxl import Workbook
    except Exception:
        # Fallback to CSV if openpyxl is unavailable
        si = io.StringIO()
        cw = csv.writer(si)
        cw.writerow(['email', 'provider'])
        for e in emails:
            cw.writerow([e, detect_provider(e)])
        mem = io.BytesIO(si.getvalue().encode('utf-8'))
        mem.seek(0)
        return send_file(
            mem,
            mimetype='text/csv',
            as_attachment=True,
            download_name='extracted_emails.csv',
        )

    wb = Workbook()
    ws = wb.active
    ws.title = 'emails'
    headers = ['email', 'provider', 'role', 'mx']
    ws.append(headers)
    for e in emails:
        m = meta.get(e, {})
        ws.append([
            e,
            detect_provider(e),
            bool(m.get('role')),
            m.get('mx')
        ])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    return send_file(
        buf,
        mimetype=(
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        ),
        as_attachment=True,
        download_name=f'extracted_emails_{ts}.xlsx',
    )


@app.route('/reset')
def reset() -> Response:
    # Clear user session data to reset license and results
    for k in ['extracted', 'invalid', 'meta', 'unlocked', 'scrape_quota']:
        try:
            session.pop(k, None)
        except Exception:
            pass
    return redirect(url_for('index'))


@app.route('/healthz')
def healthz() -> Response:
    return jsonify({'ok': True}), 200


@app.route('/metrics')
def metrics() -> tuple[bytes, int, dict[str, str]]:
    return generate_latest(), 200, {'Content-Type': CONTENT_TYPE_LATEST}


@app.route('/admin/payments/verify', methods=['POST'])
@admin_auth_required
def admin_verify() -> Response:
    txid = request.form.get('txid')
    if not txid:
        return jsonify({'error': 'txid required'}), 400
    info = mark_verified(txid, verifier='admin')
    if not info:
        return jsonify({'error': 'not found'}), 404
    return jsonify({'ok': True, 'payment': info})


@app.route('/admin/payments/verify-online', methods=['POST'])
@admin_auth_required
def admin_verify_online() -> Response:
    txid = request.form.get('txid')
    if not txid:
        return jsonify({'error': 'txid required'}), 400
    ok = verify_trc20_tx_online(txid)
    if ok:
        info = mark_verified(txid, verifier='trc20-auto')
        return jsonify({'ok': True, 'payment': info})
    return (
        jsonify({'ok': False, 'error': 'not found or not confirmed on chain'}),
        404,
    )


if __name__ == '__main__':
    app.run(debug=True, port=5000)
