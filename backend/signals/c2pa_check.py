"""C2PA content credentials check, for media FILES (not YouTube links).

We can't download YouTube videos (against YouTube's terms), so this runs on files
a user uploads to /check-file, e.g. a clip shared with them on WhatsApp.

Requires: pip install c2pa-python
AI generation is declared in C2PA via digitalSourceType on c2pa.created / c2pa.edited
actions, using the IPTC terms below.
"""
import json

from . import result

AI_SOURCE_TYPES = (
    "trainedAlgorithmicMedia",            # fully AI-generated
    "compositeWithTrainedAlgorithmicMedia",  # partly AI
    "algorithmicMedia",
)
CAMERA_SOURCE_TYPES = ("digitalCapture",)


def check_file(path, mime_type):
    try:
        import c2pa  # noqa: F401
    except ImportError:
        return result(status="not_configured", detail="pip install c2pa-python")
    try:
        from c2pa import Reader
        with open(path, "rb") as f:
            reader = Reader(mime_type, f)
            store = json.loads(reader.json())
    except Exception as e:
        # Most files simply have no manifest; the library raises in that case.
        msg = str(e)
        if "ManifestNotFound" in msg or "no JUMBF" in msg.lower() or "not found" in msg.lower():
            return result(status="ok", value=None, detail="No content credentials in file")
        return result(status="error", detail=type(e).__name__)
    return interpret_manifest_store(store)


def interpret_manifest_store(store):
    """Pure function, unit-testable without the native library."""
    active = store.get("active_manifest")
    manifest = (store.get("manifests") or {}).get(active) or {}
    source_types = []
    for a in manifest.get("assertions", []):
        if not str(a.get("label", "")).startswith("c2pa.actions"):
            continue
        for act in (a.get("data") or {}).get("actions", []):
            dst = act.get("digitalSourceType") or ""
            source_types.append(dst.rsplit("/", 1)[-1])
    signer = (manifest.get("signature_info") or {}).get("issuer", "unknown signer")
    validation_errors = store.get("validation_status") or []

    if any(t in AI_SOURCE_TYPES for t in source_types):
        detail = f"Signed by {signer}: declares AI generation"
        if validation_errors:
            detail += " (signature has validation warnings)"
        return result(status="ok", value=True, detail=detail)
    if any(t in CAMERA_SOURCE_TYPES for t in source_types):
        # Camera capture is reported as a fact but never turned into a "Real" badge.
        return result(status="ok", value=False, detail=f"Signed by {signer}: camera capture, no AI declared")
    return result(status="ok", value=False, detail=f"Signed by {signer}: no AI declared")
