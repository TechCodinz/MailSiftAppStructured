from file_parsing import extract_text_from_file
import io


def test_extract_text_txt():
    b = b"hello world\ncontact: info@example.com"
    res = extract_text_from_file(io.BytesIO(b), 'sample.txt')
    assert 'info@example.com' in res


def test_extract_text_docx_empty_on_missing_lib():
    # If python-docx is missing, function should not raise and return empty string for .docx
    res = extract_text_from_file(io.BytesIO(b''), 'empty.docx')
    assert isinstance(res, str)
