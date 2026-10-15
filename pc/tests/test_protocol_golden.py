"""
The Wi-Fi protocol bytes must match the Kotlin client (RemoteTests.framingMatchesTheGoldenBytes).
    python tests/test_protocol_golden.py
"""
import struct

import numpy as np

hello = b"MHR1" + bytes([2]) + b"ab"
assert hello == bytes([0x4D, 0x48, 0x52, 0x31, 2, 0x61, 0x62])

req = struct.pack("<II", 1, 2) + np.array([1.0, -2.0], dtype="<f4").tobytes()
assert req == bytes([1, 0, 0, 0, 2, 0, 0, 0, 0, 0, 0x80, 0x3F, 0, 0, 0, 0xC0]), req.hex()
print("PROTOCOL GOLDEN BYTES OK")
