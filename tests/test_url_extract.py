from app import extract_emails_from_html


def test_extract_from_html_simple() -> None:
    html = (
        '<html><body>Contact: '
        '<a href="mailto:foo@example.com">foo</a> '
        'or bar (at) example (dot) org'
        '</body></html>'
    )
    valid, invalid = extract_emails_from_html(html)
    assert 'foo@example.com' in valid
    assert 'bar@example.org' in valid


def test_provider_detection() -> None:
    from app import detect_provider
    assert detect_provider('alice@gmail.com') == 'gmail'
    assert detect_provider('bob@yahoo.com') == 'yahoo'
    assert detect_provider('ceo@startup.io') in ('corporate', 'other')
