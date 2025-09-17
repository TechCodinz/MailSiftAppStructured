from server import app
from _pytest.monkeypatch import MonkeyPatch


def test_index_get() -> None:
    client = app.test_client()
    r = client.get('/')
    assert r.status_code == 200


def test_admin_unauthorized() -> None:
    client = app.test_client()
    r = client.get('/admin/payments')
    assert r.status_code == 401


def test_admin_authorized_header(monkeypatch: MonkeyPatch) -> None:
    client = app.test_client()
    # set admin key
    monkeypatch.setenv('MAILSIFT_ADMIN_KEY', 'adminkey')
    r = client.get('/admin/payments', headers={'X-Admin-Key': 'adminkey'})
    assert r.status_code == 200
