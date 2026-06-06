"""ASTM E1381/E1394 low-level codec: control characters, framing, checksums.

The gateway operates at the *frame* and *record-line* level. It reassembles
inbound frames into a complete ASTM message (a list of record lines such as
``H|\\^&|...``, ``P|1|...``, ``R|1|...``, ``L|1|N``) and forwards the raw text
to CARE, which performs the deep record parsing. For outbound orders the
backend builds the record lines and the gateway frames them for transmission.

References: ASTM E1381 (low-level transport) and E1394 (record content).
"""

from __future__ import annotations

# --- Control characters (ASTM E1381) ---
ENQ = b"\x05"
ACK = b"\x06"
NAK = b"\x15"
EOT = b"\x04"
STX = b"\x02"
ETX = b"\x03"
ETB = b"\x17"
CR = b"\x0d"
LF = b"\x0a"

CRLF = CR + LF

# Maximum frame numbers cycle 0-7 per the standard.
FRAME_NUMBER_MODULO = 8

# Default E1394 delimiters: field '|', repeat '\', component '^', escape '&'.
DEFAULT_DELIMITERS = ("|", "\\", "^", "&")


def make_checksum(payload: bytes) -> bytes:
    """Compute the 2-character ASTM checksum for *payload*.

    *payload* is the frame content used for the checksum: the frame number,
    the record text, and the terminating ``ETX``/``ETB`` byte. The checksum is
    the sum of those bytes modulo 256, formatted as two uppercase hex digits.
    """
    total = sum(payload) % 256
    return f"{total:02X}".encode("ascii")


def build_frame(record_text: str, frame_number: int, *, last: bool = True) -> bytes:
    """Build a complete ASTM frame for a single record.

    ``STX <FN> <record_text> <ETX|ETB> <C1><C2> CR LF``

    *frame_number* is taken modulo 8. ``last=True`` terminates the frame with
    ETX (final frame of the record); ``last=False`` uses ETB (intermediate).
    """
    fn = str(frame_number % FRAME_NUMBER_MODULO).encode("ascii")
    terminator = ETX if last else ETB
    body = fn + record_text.encode("ascii") + terminator
    checksum = make_checksum(body)
    return STX + body + checksum + CRLF


def parse_frame(frame: bytes) -> tuple[int, str, bool, bool]:
    """Parse a raw frame (``STX`` … ``CR LF``) into its components.

    Returns ``(frame_number, record_text, is_last, checksum_ok)`` where
    *is_last* is True for ETX-terminated frames and *checksum_ok* indicates
    whether the embedded checksum matches the computed one.

    Raises :class:`ValueError` if the frame structure is malformed.
    """
    data = frame
    if data.startswith(STX):
        data = data[1:]
    if data.endswith(CRLF):
        data = data[:-2]
    elif data.endswith(CR):
        data = data[:-1]

    # Locate the terminator (ETX or ETB) that precedes the 2-char checksum.
    term_index = None
    is_last = True
    for i in range(len(data) - 1, -1, -1):
        byte = data[i : i + 1]
        if byte == ETX:
            term_index = i
            is_last = True
            break
        if byte == ETB:
            term_index = i
            is_last = False
            break
    if term_index is None:
        raise ValueError("ASTM frame missing ETX/ETB terminator")

    checksum_field = data[term_index + 1 :]
    body = data[:term_index] + data[term_index : term_index + 1]  # include terminator
    if not body:
        raise ValueError("ASTM frame has empty body")

    fn_byte = body[0:1]
    try:
        frame_number = int(fn_byte.decode("ascii"))
    except ValueError as exc:
        raise ValueError(f"Invalid ASTM frame number {fn_byte!r}") from exc

    record_text = body[1:-1].decode("ascii", errors="replace")
    expected = make_checksum(body)
    checksum_ok = checksum_field.upper() == expected
    return frame_number, record_text, is_last, checksum_ok


def split_fields(record_line: str, delimiters: tuple[str, str, str, str] = DEFAULT_DELIMITERS) -> list[str]:
    """Split a record line into its top-level fields by the field delimiter."""
    field_delim = delimiters[0]
    return record_line.split(field_delim)


def record_type(record_line: str) -> str:
    """Return the single-letter record type (H, P, O, R, C, L, Q, …)."""
    return record_line[:1].upper() if record_line else ""


def detect_delimiters(header_line: str) -> tuple[str, str, str, str]:
    """Extract the delimiter set from an ASTM header (``H``) record.

    The character immediately after ``H`` is the field delimiter; the next
    field encodes repeat/component/escape delimiters, e.g. ``H|\\^&|``.
    Falls back to :data:`DEFAULT_DELIMITERS` when the header is non-standard.
    """
    if len(header_line) < 2 or record_type(header_line) != "H":
        return DEFAULT_DELIMITERS
    field_delim = header_line[1]
    parts = header_line.split(field_delim)
    if len(parts) > 1 and len(parts[1]) >= 3:
        repeat, component, escape = parts[1][0], parts[1][1], parts[1][2]
        return (field_delim, repeat, component, escape)
    return (field_delim, "\\", "^", "&")
