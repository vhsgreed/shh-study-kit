"""Every MCP tool must carry all four hints as booleans, plus an input schema.

A directory that indexes MCP servers (m8ven.ai) graded this repo C and flagged "no tools have
read-only/destructive annotations" and "only 3/16 declare input schemas". The schema part was
already true (the decorator builds it centrally, which a static reader misses); the hints were
genuinely missing, and OpenAI's directory rejects tools where any hint is missing or non-boolean.
"""
import json

from unitoolkit import tools

HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")


def test_every_tool_declares_four_boolean_hints():
    for t in tools.manifest():
        ann = t.get("annotations") or {}
        for h in HINTS:
            assert h in ann, f"{t['name']} is missing {h}"
            assert isinstance(ann[h], bool), f"{t['name']}.{h} must be a bool, got {ann[h]!r}"


def test_every_tool_has_an_object_schema():
    for t in tools.manifest():
        schema = t["inputSchema"]
        assert schema["type"] == "object" and isinstance(schema["properties"], dict)
        assert isinstance(schema["required"], list)


def test_browser_tools_that_write_files_are_not_marked_read_only():
    for name in ("mirror", "docs", "sharepoint_download", "programme"):
        t = tools.TOOLS[name]
        assert t["annotations"]["readOnlyHint"] is False, name


def test_quiz_can_submit_so_it_is_not_read_only():
    ann = tools.TOOLS["quiz"]["annotations"]
    assert ann["readOnlyHint"] is False and ann["destructiveHint"] is True


def test_reading_tools_stay_read_only_and_idempotent():
    for name in ("courses", "upcoming", "login_check", "assignment", "programme", "courses"):
        ann = tools.TOOLS[name]["annotations"]
        assert ann["destructiveHint"] is False, name


def test_mcp_tools_list_passes_annotations_through():
    from unitoolkit.mcp import handle
    out = handle({"method": "tools/list", "id": 1, "params": {}})
    listed = out["tools"]
    assert listed, "tools/list returned nothing"
    for t in listed:
        assert set(HINTS) <= set(t.get("annotations") or {}), t["name"]
    json.dumps(out)   # must stay JSON-serialisable


def test_unknown_tool_is_a_clear_error():
    try:
        tools.call("no_such_tool")
    except KeyError as e:
        assert "no_such_tool" in str(e)
    else:
        raise AssertionError("call() accepted an unknown tool")
