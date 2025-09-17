from flask import Flask, render_template, request, redirect, url_for, session, jsonify, abort, send_file
import os
from app import extract_emails_from_text, extract_emails_from_html, group_by_provider, session_increment_scrape_quota, detect_provider
from file_parsing import extract_text_from_file
from payments import record_payment, get_payment, mark_verified, list_payments, verify_admin_key, verify_trc20_tx_online
from functools import wraps
import io
import csv
import json
import time
import random
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get('MAILSIFT_SECRET', 'dev-secret-key')
SETTINGS_FILE = os.environ.get('MAILSIFT_SETTINGS_FILE') or os.path.join(os.path.dirname(__file__), 'settings.json')


def admin_auth_required(f):
    from functools import wraps
    @wraps(f)
    def inner(*args, **kwargs):
        # Support either simple key header or HTTP basic auth
        key = request.args.get('key') or request.form.get('key') or request.headers.get('X-Admin-Key')
        if not key:
            # try basic auth
            auth = request.authorization
            if auth and auth.password:
                key = auth.password
        if not verify_admin_key(key or ''):
            return jsonify({'error': 'unauthorized'}), 401
        return f(*args, **kwargs)
    return inner


def _load_settings():
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_settings(data):
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception:
        return False


def get_settings():
    # Merge env defaults with file settings
    data = _load_settings()
    defaults = {
        'price_usdt': float(os.environ.get('PRICE_USDT', '30') or 30),
        'free_scrape_limit': int(os.environ.get('FREE_SCRAPE_LIMIT', '3') or 3),
        'preview_sample_size': int(os.environ.get('PREVIEW_SAMPLE_SIZE', '20') or 20),
        'wallets': {
            'btc': os.environ.get('WALLET_BTC', ''),
            'trc20': os.environ.get('MAILSIFT_RECEIVE_ADDRESS', ''),
            'eth': os.environ.get('WALLET_ETH', ''),
        },
        'tiers': data.get('tiers') or [
            {'id': 'free', 'name': 'Free', 'monthly_emails': 500, 'monthly_domains': 500, 'features': ['basic_extraction', 'csv_export'], 'price_usd': 0},
            {'id': 'starter', 'name': 'Starter', 'monthly_emails': 5000, 'monthly_domains': 1000, 'features': ['basic_verification', 'limited_api', 'extra_fields'], 'price_usd_range': '20-50'},
            {'id': 'pro', 'name': 'Pro', 'monthly_emails': 50000, 'features': ['full_verification', 'richer_data', 'priority_support'], 'price_usd_range': '100-300'},
            {'id': 'business', 'name': 'Business/Enterprise', 'monthly_emails': 100000, 'features': ['advanced_features', 'SLA', 'dedicated_support'], 'price_usd_range': '500-2000+'},
        ],
        'payg_per_1000_usd': data.get('payg_per_1000_usd', '1-5'),
    }
    # Apply file overrides
    defaults.update({k: v for k, v in data.items() if k != 'wallets'})
    if isinstance(data.get('wallets'), dict):
        defaults['wallets'].update(data['wallets'])
    return defaults


def get_pricing_context():
    """Provide template context for pricing, wallets and tiers."""
    s = get_settings()
    return {
        'price_usdt': s['price_usdt'],
        'wallet_btc': s['wallets'].get('btc', ''),
        'wallet_trc20': s['wallets'].get('trc20', ''),
        'wallet_eth': s['wallets'].get('eth', ''),
        'tiers': s.get('tiers', []),
        'payg_per_1000_usd': s.get('payg_per_1000_usd'),
        'free_limit': s.get('free_scrape_limit'),
        'preview_sample_size': s.get('preview_sample_size'),
    }


@app.template_filter('mask_email')
def mask_email_filter(email):
    try:
        local, domain = email.split('@', 1)
        local_mask = (local[:1] + '***') if len(local) >= 1 else '***'
        parts = domain.split('.')
        first = parts[0] if parts else ''
        tld = parts[-1] if parts else ''
        first_mask = (first[:1] + '***') if len(first) >= 1 else '***'
        suffix = ('.' + tld) if tld else ''
        return f"{local_mask}@{first_mask}{suffix}"
    except Exception:
        return '***'


@app.route('/paywall', methods=['GET'])
def paywall_view():
    return render_template('paywall.html', **get_pricing_context())


@app.route('/')
def index():
    # show any current session results
    results = None
    if 'extracted' in session:
        extracted = session.get('extracted', [])
        meta = session.get('meta', {})
        s = get_settings()
        if not session.get('unlocked'):
            # Show only a preview sample
            preview_n = max(1, int(s.get('preview_sample_size') or 20))
            sample = extracted[:preview_n]
            provider_groups = group_by_provider(sample)
            results = {'valid': provider_groups, 'meta': meta, 'invalid': session.get('invalid', []), 'preview': True, 'preview_n': preview_n}
        else:
            results = {'valid': group_by_provider(extracted), 'meta': meta, 'invalid': session.get('invalid', [])}
    return render_template('index.html', results=results)


@app.route('/scrape', methods=['POST'])
def scrape():
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

    # If we have URLs, enforce paywall after a small free quota and then fetch concurrently (best-effort)
    if url_list:
        # paywall gating for URL scraping usage
        s = get_settings()
        free_limit = int(s.get('free_scrape_limit') or 3)
        quota = session.get('scrape_quota', 0)
        planned = len(url_list)
        if not session.get('unlocked') and (quota >= free_limit or (quota + planned) > free_limit):
            return render_template('paywall.html', error=f'Free limit reached ({free_limit} site fetches). Please unlock to continue.', **get_pricing_context())
        try:
            import requests
            from concurrent.futures import ThreadPoolExecutor, as_completed

            headers = {'User-Agent': 'MailSift/1.0 (+https://example)'}

            def fetch(url):
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
                    # increment quota per successful fetch
                    session_increment_scrape_quota()
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
    session['meta'] = session.get('meta', {})

    # Apply preview gating to results list for display
    all_emails = session.get('extracted', [])
    s = get_settings()
    if not session.get('unlocked'):
        preview_n = max(1, int(s.get('preview_sample_size') or 20))
        show = all_emails[:preview_n]
        provider_groups = group_by_provider(show)
        results = {'valid': provider_groups, 'per_site': per_site or None, 'invalid': session.get('invalid', []), 'meta': session.get('meta', {}), 'preview': True, 'preview_n': preview_n, 'quota': session.get('scrape_quota', 0), 'free_limit': s.get('free_scrape_limit')}
    else:
        provider_groups = group_by_provider(all_emails)
        results = {'valid': provider_groups, 'per_site': per_site or None, 'invalid': session.get('invalid', []), 'meta': session.get('meta', {}), 'quota': session.get('scrape_quota', 0), 'free_limit': s.get('free_scrape_limit')}
    return render_template('index.html', results=results)


@app.route('/pay', methods=['POST'])
def pay():
    if request.method == 'POST':
        txid = request.form.get('txid')
        address = request.form.get('contact') or request.form.get('address')
        amount = float(request.form.get('amount') or 0)
        contact = request.form.get('contact')
        asset = (request.form.get('asset') or '').strip()
        # require contact email so we can email the license automatically
        if not contact or '@' not in contact:
            return render_template('paywall.html', error='Contact email required to deliver license.', **get_pricing_context())
        if not txid or not address:
            return render_template('paywall.html', error='txid and address required', **get_pricing_context())
        rec = record_payment(txid, address, amount)
        # attach contact if provided
        data = list_payments()
        if txid in data and contact:
            data[txid]['contact'] = contact
            # save back
            from payments import _save_payments
            _save_payments(data)
        # Attempt automatic verification for USDT (TRC20) if receiving address configured
        auto_msg = None
        try:
            expected_addr = os.environ.get('MAILSIFT_RECEIVE_ADDRESS')
            if expected_addr and ('trc20' in asset.lower() or 'usdt' in asset.lower() or asset.strip() == ''):
                ok = verify_trc20_tx_online(txid)
                if ok:
                    info = mark_verified(txid, verifier='trc20-auto')
                    session['unlocked'] = True
                    auto_msg = 'Payment verified on-chain. License emailed to your contact.'
        except Exception:
            pass
        return render_template('paywall.html', error=(auto_msg or ('Payment received. Awaiting verification. TXID: ' + str(txid))), pending_txid=txid, **get_pricing_context())
    return render_template('paywall.html')


@app.route('/redeem', methods=['POST'])
def redeem():
    key = request.form.get('license') or request.form.get('txid')
    payments = list_payments()
    for txid, info in payments.items():
        if info.get('license') == key or txid == key:
            session['unlocked'] = True
            return render_template('paywall.html', error='Unlocked. License applied.', **get_pricing_context())
    return render_template('paywall.html', error='Invalid license or txid', **get_pricing_context())


@app.route('/admin/payments', methods=['GET', 'POST'])
@admin_auth_required
def admin_payments():
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
def download():
    if not session.get('unlocked'):
        return render_template('paywall.html', error='Please unlock to download results.', **get_pricing_context())
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
    return send_file(mem, mimetype='text/csv', as_attachment=True, download_name='extracted_emails.csv')


@app.route('/download/json')
def download_json():
    if not session.get('unlocked'):
        return render_template('paywall.html', error='Please unlock to download results.', **get_pricing_context())
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
    return send_file(mem, mimetype='application/json', as_attachment=True, download_name='extracted_emails.json')


@app.route('/download/excel')
def download_excel():
    if not session.get('unlocked'):
        return render_template('paywall.html', error='Please unlock to download results.', **get_pricing_context())
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
        return send_file(mem, mimetype='text/csv', as_attachment=True, download_name='extracted_emails.csv')

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
    return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=f'extracted_emails_{ts}.xlsx')


@app.route('/reset')
def reset():
    # Clear user session data to reset license and results
    for k in ['extracted', 'invalid', 'meta', 'unlocked', 'scrape_quota']:
        try:
            session.pop(k, None)
        except Exception:
            pass
    return redirect(url_for('index'))


@app.route('/admin/payments/verify', methods=['POST'])
@admin_auth_required
def admin_verify():
    txid = request.form.get('txid')
    if not txid:
        return jsonify({'error': 'txid required'}), 400
    info = mark_verified(txid, verifier='admin')
    if not info:
        return jsonify({'error': 'not found'}), 404
    return jsonify({'ok': True, 'payment': info})


@app.route('/admin/payments/verify-online', methods=['POST'])
@admin_auth_required
def admin_verify_online():
    txid = request.form.get('txid')
    if not txid:
        return jsonify({'error': 'txid required'}), 400
    ok = verify_trc20_tx_online(txid)
    if ok:
        info = mark_verified(txid, verifier='trc20-auto')
        return jsonify({'ok': True, 'payment': info})
    return jsonify({'ok': False, 'error': 'not found or not confirmed on chain'}), 404


@app.route('/pay/status/<txid>', methods=['GET'])
def pay_status(txid):
    info = get_payment(txid)
    if not info:
        return jsonify({'ok': False, 'verified': False}), 404
    return jsonify({'ok': True, 'verified': bool(info.get('verified')), 'license': info.get('license')})


@app.route('/admin/settings', methods=['GET', 'POST'])
@admin_auth_required
def admin_settings():
    s = get_settings()
    if request.method == 'POST':
        # Update selected fields
        try:
            price = request.form.get('price_usdt')
            free_lim = request.form.get('free_scrape_limit')
            preview_n = request.form.get('preview_sample_size')
            wallets = {
                'btc': request.form.get('wallet_btc') or s['wallets'].get('btc', ''),
                'trc20': request.form.get('wallet_trc20') or s['wallets'].get('trc20', ''),
                'eth': request.form.get('wallet_eth') or s['wallets'].get('eth', ''),
            }
            data = _load_settings()
            if price:
                data['price_usdt'] = float(price)
            if free_lim:
                data['free_scrape_limit'] = int(free_lim)
            if preview_n:
                data['preview_sample_size'] = int(preview_n)
            data['wallets'] = wallets
            _save_settings(data)
            return redirect(url_for('admin_settings'))
        except Exception:
            pass
    return render_template('admin_settings.html', settings=get_settings())


if __name__ == '__main__':
    app.run(debug=True, port=5000)
