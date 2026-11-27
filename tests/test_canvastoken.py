"""Offline tests for the Canvas token module: rotation policy and the 30-day cap, no network."""
import pathlib

from unitoolkit import canvastoken as ct


def test_rotation_policy():
    assert ct.needs_rotation({"installed": False}) is True
    assert ct.needs_rotation({"installed": True, "valid": False}) is True
    assert ct.needs_rotation({"installed": True, "valid": True, "days_left": 10}) is True
    assert ct.needs_rotation({"installed": True, "valid": True, "days_left": 14}) is True
    assert ct.needs_rotation({"installed": True, "valid": True, "days_left": 15}) is False
    assert ct.needs_rotation({"installed": True, "valid": True}) is False   # no expiry known


def test_canvas_caps_student_tokens_at_30_days():
    # SHH's Canvas answers "Expiration date cannot be more than 30 days in the future"; asking for
    # 120 days silently clamped is what keeps the weekly rotation honest.
    assert ct.MAX_DAYS == 30


def test_missing_token_file_is_reported_with_a_fix(tmp_path, monkeypatch):
    monkeypatch.setattr(ct, "SECRETS", tmp_path / "nope")
    st = ct.status()
    assert st["installed"] is False and "create" in st["fix"]


def test_value_never_appears_in_status(tmp_path, monkeypatch):
    secret = tmp_path / "tok"
    secret.write_text("REAL-SECRET-VALUE\n")
    monkeypatch.setattr(ct, "SECRETS", secret)
    monkeypatch.setattr(ct, "META", tmp_path / "meta.json")
    monkeypatch.setattr(ct, "_api", lambda path, token=None: {"id": 1, "name": "T"})
    st = ct.status()
    assert st["valid"] is True and st["user"] == "T"
    assert "REAL-SECRET-VALUE" not in repr(st)
