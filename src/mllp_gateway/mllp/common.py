"""HL7 message validation helpers."""

__all__ = ["validate_hl7"]


def validate_hl7(raw: str) -> str | None:
    """Validate an HL7 message. Returns an error string, or None if valid."""
    if not raw or not raw.strip():
        return "HL7 message is empty"
    lines = raw.replace("\n", "\r").split("\r")
    lines = [l for l in lines if l.strip()]
    if not lines:
        return "HL7 message contains no segments"
    if not lines[0].startswith("MSH"):
        return "HL7 message must start with MSH segment"
    if len(lines[0]) < 4:
        return "MSH segment is too short — missing field separator"
    return None
