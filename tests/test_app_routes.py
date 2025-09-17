import pytest
from typing import Generator
from flask.testing import FlaskClient
from app import app


@pytest.fixture()  # type: ignore[misc]
def client() -> Generator[FlaskClient, None, None]:
    app.config['TESTING'] = True
    with app.test_client() as c:
        with app.app_context():
            yield c


def test_index_get(client: FlaskClient) -> None:
    r = client.get('/')
    assert r.status_code == 200


def test_unlock_offline_token_flow(client: FlaskClient) -> None:
    # simulate posting an invalid token
    r = client.post('/unlock', data={'license_key': 'bad.token'})
    assert r.status_code == 200
