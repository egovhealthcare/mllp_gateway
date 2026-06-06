"""ASTM E1381/E1394 protocol support for the gateway.

The gateway works at the frame/record-line level: it reassembles inbound
frames into a raw ASTM message and forwards the text to CARE, and it frames
outbound order records built by CARE. Deep record parsing lives in the CARE
backend.
"""

from mllp_gateway.astm.session import ASTMSession

__all__ = ["ASTMSession"]
