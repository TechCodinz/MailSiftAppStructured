import pytest
from app import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        with app.app_context():
            yield c


def test_index_get(client):
    r = client.get('/')
    assert r.status_code == 200


def test_unlock_offline_token_flow(client):
    # simulate posting an invalid token
    r = client.post('/unlock', data={'license_key': 'bad.token'})
    assert r.status_code == 200
