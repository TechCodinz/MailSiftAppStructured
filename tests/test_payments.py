import os
import tempfile
from payments import record_payment, get_payment, mark_verified, list_payments, generate_license_for


def test_record_and_get_payment(tmp_path, monkeypatch):
    # point PAYMENTS_FILE to a temp file
    p = tmp_path / 'payments.json'
    monkeypatch.setenv('MAILSIFT_SECRET', 'test-secret')
    # monkeypatch the payments file path by editing the module attr
    import payments as P
    P.PAYMENTS_FILE = str(p)

    rec = record_payment('tx1', 'TXYZ', 5.0)
    assert rec['txid'] == 'tx1'
    loaded = get_payment('tx1')
    assert loaded['address'] == 'TXYZ'

    verified = mark_verified('tx1', verifier='tests')
    assert verified['verified'] is True
    assert 'license' in verified


def test_generate_license_uniqueness():
    a = generate_license_for('tx-a')
    b = generate_license_for('tx-b')
    assert a != b
