"""Offline tests for canvas_file: the API route, not the web /download route."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unitoolkit import canvas, tools  # noqa: E402


def test_file_meta_asks_the_api_not_the_download_path(monkeypatch):
    seen = {}

    def fake_get(path, **params):
        seen['path'] = path
        return {'id': 455259, 'display_name': 'Risk factors for persistent pain.pdf',
                'size': 12, 'url': 'https://files.example/x?verifier=1'}

    monkeypatch.setattr(canvas, 'get', fake_get)
    meta = canvas.file_meta(2416, 455259)
    assert seen['path'] == 'courses/2416/files/455259'
    assert 'download' not in seen['path']
    assert meta['url'].startswith('https://files.example/')


def test_save_file_writes_the_bytes_and_sanitises_the_name(tmp_path, monkeypatch):
    monkeypatch.setattr(canvas, 'get', lambda path, **p: {
        'display_name': 'Nurses’ communication/families: ICU.pdf',
        'url': 'https://files.example/y'})
    fetched = {}

    def fake_req(url, raw=False):
        fetched['url'] = url
        return b'%PDF-1.7 body', {}

    monkeypatch.setattr(canvas, '_req', fake_req)
    out = canvas.save_file(2416, 455297, str(tmp_path))
    assert fetched['url'] == 'https://files.example/y'
    p = Path(out['path'])
    assert p.exists() and p.read_bytes() == b'%PDF-1.7 body'
    assert '/' not in p.name and ':' not in p.name
    assert out['bytes'] == len(b'%PDF-1.7 body')


def test_save_file_refuses_a_file_without_url(tmp_path, monkeypatch):
    monkeypatch.setattr(canvas, 'get', lambda path, **p: {'display_name': 'locked.pdf', 'url': None})
    try:
        canvas.save_file(2416, 1, str(tmp_path))
    except ValueError as exc:
        assert 'no download url' in str(exc)
    else:
        raise AssertionError('expected ValueError')


def test_tool_is_registered_with_a_course_and_file_id():
    t = tools.TOOLS['canvas_file']
    assert t['inputSchema']['required'] == ['course', 'file_id']
    assert set(t['inputSchema']['properties']) == {'course', 'file_id', 'out'}
