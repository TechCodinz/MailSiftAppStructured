from app import app


def test_app_importable() -> None:
    # Basic smoke test: the Flask app object should exist and have a route map
    assert hasattr(app, "url_map")
    # Ensure index endpoint exists by name
    endpoint_names = [r.endpoint for r in app.url_map.iter_rules()]
    assert "index" in endpoint_names or any("index" in n for n in endpoint_names)
