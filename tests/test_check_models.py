from market_agents.check_models import check


def test_check_classifies_results():
    ok = lambda *a: (200, {"choices": [{"message": {"tool_calls": [
        {"id": "1", "function": {"name": "ping", "arguments": '{"text":"xin chào"}'}}]}}]})
    text = lambda *a: (200, {"choices": [{"message": {"content": "xin chào"}}]})
    fail = lambda *a: (429, {"error": {"message": "Provider returned error", "metadata": {"raw": "busy"}}})
    assert check("k", "m", post=ok)[0] == "OK"
    assert check("k", "m", post=text)[0] == "NO_TOOLS"
    result, note, _ = check("k", "m", post=fail)
    assert result == "FAIL" and "busy" in note
