from flask import Flask, render_template, request, session
import re
import os
from collections import defaultdict


app = Flask(__name__)
app.secret_key = os.environ.get('MAILSIFT_SECRET', 'dev-secret-key')


# Minimal, deterministic email utilities used by tests.
EMAIL_RE = re.compile(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")


def normalize_deobfuscate(text: str) -> str:
    if not text:
        return text
    s = text
    s = s.replace('&#64;', '@').replace('&nbsp;', ' ')
    s = s.replace('\uFF20', '@')
    s = re.sub(r"\(at\)|\[at\]|\{at\}", '@', s, flags=re.IGNORECASE)
    s = re.sub(r"\(dot\)|\[dot\]|\{dot\}", '.', s, flags=re.IGNORECASE)
    s = s.replace('[', '').replace(']', '')
    s = s.replace('{', '').replace('}', '')
    s = re.sub(r"\s+", ' ', s)
    return s.strip()


def extract_emails_from_text(text: str):
    if not text:
        return [], []
    cleaned = normalize_deobfuscate(text)
    # compact spaced punctuation introduced by normalization, e.g. 'name @ domain . com' -> 'name@domain.com'
    compacted = re.sub(r"(\w)\s*@\s*(\w)", r"\1@\2", cleaned)
    compacted = re.sub(r"(\w)\s*\.\s*(\w)", r"\1.\2", compacted)
    candidates = set()

    # find obvious emails in both cleaned and compacted forms
    for m in EMAIL_RE.finditer(cleaned):
        candidates.add(m.group(0))
    for m in EMAIL_RE.finditer(compacted):
        candidates.add(m.group(0))

    for m in re.finditer(r'mailto:([^\s"<>]+)', text, flags=re.IGNORECASE):
        candidates.add(m.group(1))
    # also pick up mailto occurrences that may be inside cleaned/compacted text
    for m in re.finditer(r'mailto:([^\s"<>]+)', cleaned, flags=re.IGNORECASE):
        candidates.add(m.group(1))
    for m in re.finditer(r'mailto:([^\s"<>]+)', compacted, flags=re.IGNORECASE):
        candidates.add(m.group(1))

    # allow common obfuscated forms like 'name (at) domain (dot) com'
    # place hyphen at end of class or escape it to avoid a range error
    obf = re.compile(r'([A-Za-z0-9_.+\-]+)\s*(?:\(|\[)?\s*at\s*(?:\)|\])?\s*([A-Za-z0-9_.\-\s\[\]\(\)]*)', flags=re.IGNORECASE)
    # run obfuscated pattern on the normalized text so (at)/(dot) are converted
    for m in obf.finditer(cleaned):
        local = m.group(1)
        domain_raw = m.group(2)
        if '@' in domain_raw:
            continue
        if not ('.' in domain_raw or re.search(r'\bdot\b', domain_raw, flags=re.IGNORECASE)):
            continue
        domain = re.sub(r'(?:\s*(?:\(|\[)?\s*dot\s*(?:\)|\])?\s*)', '.', domain_raw, flags=re.IGNORECASE)
        domain = domain.replace(' ', '').replace('..', '.')
        domain = domain.strip('.')
        if domain:
            candidates.add(f"{local}@{domain}")

    spaced = re.compile(r'([A-Za-z0-9_.+-]+)\s*@\s*([A-Za-z0-9-]+(?:\s*\.\s*[A-Za-z0-9-]+)+)')
    for m in spaced.finditer(cleaned):
        addr = m.group(1) + '@' + re.sub(r'\s*\.\s*', '.', m.group(2))
        candidates.add(addr)

    cleaned_cands = set()
    for c in candidates:
        c2 = c.strip(' \t\n\r<>"\'",;:()[]')
        cleaned_cands.add(c2.lower())

    valid = []
    invalid = []
    for e in sorted(cleaned_cands):
        if not re.fullmatch(EMAIL_RE, e):
            invalid.append(e)
            continue
        valid.append(e)

    return sorted(set(valid)), sorted(set(invalid))


def extract_emails_from_html(html_text: str):
    if not html_text:
        return [], []
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_text, 'html.parser')
        for s in soup(['script', 'style']):
            s.decompose()
        # preserve mailto: links (they live in href attributes, removed by get_text)
        text = soup.get_text(separator=' ')
        for a in soup.find_all('a', href=True):
            href = a['href']
            if isinstance(href, str) and href.lower().startswith('mailto:'):
                try:
                    addr = href.split(':', 1)[1]
                    text += ' ' + addr
                except Exception:
                    pass
    except Exception:
        # fallback: try to pull mailto: addresses from raw HTML before stripping tags
        mails = ' '.join(m.group(1) for m in re.finditer(r'mailto:([^\s"\'">]+)', html_text, flags=re.IGNORECASE))
        text = re.sub(r'<[^>]+>', ' ', html_text) + ' ' + mails

    # normalize and extract from the assembled text in both code paths
    return extract_emails_from_text(text)


def detect_provider(email: str) -> str:
    try:
        domain = email.split('@', 1)[1].lower()
    except Exception:
        return 'other'
    providers = {
        'gmail': ['gmail.com', 'googlemail.com'],
        'yahoo': ['yahoo.com', 'ymail.com'],
        'outlook': ['outlook.com', 'hotmail.com'],
        'icloud': ['icloud.com', 'me.com']
    }
    for name, domains in providers.items():
        for d in domains:
            if domain == d or domain.endswith('.' + d):
                return name
    disposable = ('mailinator.com', 'trashmail.com', '10minutemail.com')
    if any(domain.endswith(d) for d in disposable):
        return 'disposable'
    if domain.count('.') == 1:
        return 'corporate'
    return 'other'


def group_by_provider(emails):
    groups = defaultdict(list)
    for e in emails:
        groups[detect_provider(e)].append(e)
    return groups


def session_increment_scrape_quota(sess=None):
    # For tests we avoid touching Flask's session proxy. If a dict-like `sess` is provided,
    # use it. Otherwise use a module-level fallback dict so calling this function outside
    # of a request context is safe.
    if isinstance(sess, dict):
        target = sess
    else:
        global _test_session_fallback
        if '_test_session_fallback' not in globals():
            _test_session_fallback = {}
        target = _test_session_fallback
    q = target.get('scrape_quota', 0)
    q += 1
    target['scrape_quota'] = q
    return q


@app.route('/', methods=['GET'])
def index():
    # minimal index used by tests
    return render_template('index.html')


@app.route('/unlock', methods=['POST'])
def unlock():
    key = request.form.get('license_key', '').strip()
    if key == 'LET-ME-IN-DEV':
        session['unlocked'] = True
        return render_template('index.html')
    return render_template('paywall.html', error='Invalid license key', wallets={
        'btc': os.environ.get('MAILSIFT_WALLET_BTC'),
        'trc20': os.environ.get('MAILSIFT_WALLET_TRC20') or os.environ.get('MAILSIFT_RECEIVE_ADDRESS'),
        'eth': os.environ.get('MAILSIFT_WALLET_ETH'),
    })


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000)
