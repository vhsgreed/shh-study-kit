"""Offline tests for the OneDrive reader: browser.fetch is faked."""
import pytest

from unitoolkit import onedrive as od


def _fake(responses):
    calls = []

    def fetch(url, origin, as_json=True):
        calls.append(url)
        for key, resp in responses.items():
            if key in url:
                return resp
        return {"status": 404, "body": "nope"}
    return fetch, calls


SHARED = {"status": 200, "body": {"value": [
    {"name": "Samverkan i team.docx", "remoteItem": {
        "id": "ITEM1", "name": "Samverkan i team.docx", "size": 51000, "file": {},
        "lastModifiedDateTime": "2026-11-25T14:25:00Z",
        "lastModifiedBy": {"user": {"displayName": "Måns Rosell"}},
        "parentReference": {"driveId": "DRIVE_M"},
        "shared": {"owner": {"user": {"displayName": "Måns Rosell"}}}}},
    {"name": "Välkomstbrev HT26.pdf", "remoteItem": {
        "id": "ITEM2", "name": "Välkomstbrev HT26.pdf", "file": {},
        "lastModifiedDateTime": "2026-08-01T10:00:00Z", "parentReference": {"driveId": "DRIVE_S"}}},
]}}


def test_shared_maps_remote_items_to_owner_drive(monkeypatch):
    fetch, _ = _fake({"sharedWithMe": SHARED})
    monkeypatch.setattr(od.browser, "fetch", fetch)
    rows = od.shared()
    assert rows[0]["name"] == "Samverkan i team.docx"
    assert rows[0]["drive_id"] == "DRIVE_M" and rows[0]["item_id"] == "ITEM1"
    assert rows[0]["owner"] == "Måns Rosell"
    assert [r["name"] for r in od.shared("måns")] == ["Samverkan i team.docx"]


def test_expired_session_raises_not_empty(monkeypatch):
    fetch, _ = _fake({"sharedWithMe": {"status": 401, "body": "login.microsoftonline.com"}})
    monkeypatch.setattr(od.browser, "fetch", fetch)
    with pytest.raises(od.SessionGone):
        od.shared()


def test_paging_stops_on_repeated_cursor(monkeypatch):
    page = {"status": 200, "body": {"value": [{"name": "a", "id": "1"}],
                                    "@odata.nextLink": od.API + "me/drive/recent?skip=1"}}
    fetch, calls = _fake({"me/drive/recent": page})
    monkeypatch.setattr(od.browser, "fetch", fetch)
    rows = od.recent()
    assert len(calls) == 2 and len(rows) == 2


def test_download_never_overwrites(monkeypatch, tmp_path):
    fetch, _ = _fake({"sharedWithMe": SHARED, "me/drive/root/children": {"status": 200, "body": {"value": []}}})
    monkeypatch.setattr(od.browser, "fetch", fetch)
    monkeypatch.setattr(od.browser, "fetch_binary",
                        lambda url, origin: {"status": 200, "bytes": b"PK\x03\x04data"})
    (tmp_path / "Samverkan i team.docx").write_bytes(b"old")
    r = od.download(str(tmp_path), name="Samverkan i team.docx")
    assert "drives/DRIVE_M/items/ITEM1/content" in od.API + "drives/DRIVE_M/items/ITEM1/content"
    assert r["path"].endswith("Samverkan i team_202611251425.docx")
    assert (tmp_path / "Samverkan i team.docx").read_bytes() == b"old"


def test_ambiguous_name_asks_for_ids(monkeypatch):
    dup = {"status": 200, "body": {"value": SHARED["body"]["value"] * 1 + [
        {"name": "Samverkan i team.docx", "remoteItem": {
            "id": "ITEM9", "name": "Samverkan i team.docx", "file": {},
            "parentReference": {"driveId": "DRIVE_X"}}}]}}
    fetch, _ = _fake({"sharedWithMe": dup, "me/drive/root/children": {"status": 200, "body": {"value": []}}})
    monkeypatch.setattr(od.browser, "fetch", fetch)
    with pytest.raises(LookupError):
        od.resolve("Samverkan i team.docx")
