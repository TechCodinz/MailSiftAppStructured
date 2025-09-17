from payments import record_payment, get_payment, mark_verified, generate_license_for
from pathlib import Path
from _pytest.monkeypatch import MonkeyPatch


def test_record_and_get_payment(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    # point PAYMENTS_FILE to a temp file
    p = tmp_path / 'payments.json'
    monkeypatch.setenv('MAILSIFT_SECRET', 'test-secret')
    # monkeypatch the payments file path by editing the module attr
    import payments as P
    P.PAYMENTS_FILE = str(p)

    rec = record_payment('tx1', 'TXYZ', 5.0)
    assert rec and rec['txid'] == 'tx1'
    loaded = get_payment('tx1')
    assert loaded is not None and loaded['address'] == 'TXYZ'

    verified = mark_verified('tx1', verifier='tests')
    assert verified is not None and verified['verified'] is True
    assert verified is not None and 'license' in verified


def test_generate_license_uniqueness() -> None:
    a = generate_license_for('tx-a')
    b = generate_license_for('tx-b')
    assert a != b
