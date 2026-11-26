"""Each signal returns a dict: {"value": ..., "status": "ok" | "not_configured" | "error" | "not_applicable", "detail": str}.

value is None whenever the check did not actually run. The extension shows that as
"Not checked", never as a negative result.
"""


def result(value=None, status="ok", detail=""):
    return {"value": value, "status": status, "detail": detail}
