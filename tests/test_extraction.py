from app import extract_emails_from_text


def test_simple_extraction() -> None:
    text = 'Contact us at info@example.com and support@sub.example.org.'
    valid, invalid = extract_emails_from_text(text)
    assert 'info@example.com' in valid
    assert 'support@sub.example.org' in valid
    assert invalid == []


def test_obfuscated_extraction() -> None:
    text = 'Reach me at john (at) example (dot) com or jane[at]school.edu'
    valid, invalid = extract_emails_from_text(text)
    # With improved normalization, john@example.com should be extracted
    assert 'john@example.com' in valid
    assert any('school.edu' in e for e in valid)


def test_normalize_deobfuscate_direct() -> None:
    from app import normalize_deobfuscate
    s = 'alice [at] example [dot] com'
    out = normalize_deobfuscate(s)
    assert '@' in out and '.' in out


def test_quota_increment() -> None:
    from app import session_increment_scrape_quota
    # calling this increases a server-side session counter
    # We expect it returns an int >= 1
    val = session_increment_scrape_quota()
    assert isinstance(val, int) and val >= 1
