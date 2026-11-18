"""Announcements: the plain discussion list hides them, so the reader must ask explicitly."""
import time

from unitoolkit import canvas


def _row(i, posted, title='t'):
    return {'id': i, 'title': title, 'posted_at': posted, 'author': {'display_name': 'A'},
            'html_url': 'u', 'message': 'body'}


def test_asks_for_announcements_only(monkeypatch):
    seen = {}

    def fake_try_get(path, **params):
        seen['path'], seen['params'] = path, params
        return [_row(1, time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))]

    monkeypatch.setattr(canvas, 'try_get', fake_try_get)
    out = canvas.announcements(course=2416, days=30)
    assert seen['params']['only_announcements'] is True
    assert '/courses/2416/discussion_topics' in seen['path']
    assert len(out) == 1 and out[0]['author'] == 'A'


def test_newest_first_and_day_window(monkeypatch):
    now = time.time()
    rows = [_row(1, time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now - 40 * 86400)), 'old'),
            _row(2, time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now - 86400)), 'new')]
    monkeypatch.setattr(canvas, 'try_get', lambda *a, **k: rows)
    out = canvas.announcements(course=2416, days=7)
    assert [a['title'] for a in out] == ['new']


def test_empty_when_canvas_hides_them(monkeypatch):
    monkeypatch.setattr(canvas, 'try_get', lambda *a, **k: [])
    assert canvas.announcements(course=2416, days=30) == []


def test_discussions_skip_announcements_and_filter_read(monkeypatch):
    rows = [{'id': 1, 'title': 'group discussion', 'read_state': 'unread',
             'discussion_subentry_count': 15, 'posted_at': '2026-11-10T10:00:00Z'},
            {'id': 2, 'title': 'an announcement', 'is_announcement': True, 'read_state': 'read'},
            {'id': 3, 'title': 'already read', 'read_state': 'read',
             'discussion_subentry_count': 2}]
    monkeypatch.setattr(canvas, 'try_get', lambda *a, **k: rows)
    got = canvas.discussions(course=2415, unread_only=True)
    assert [d['title'] for d in got] == ['group discussion']
    assert got[0]['entries'] == 15 and got[0]['read_state'] == 'unread'
