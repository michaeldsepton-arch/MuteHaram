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
hello2 = b"MHR2" + bytes([2]) + b"ab"
assert hello2 == bytes([0x4D, 0x48, 0x52, 0x32, 2, 0x61, 0x62])
# version 2 request: seq 1, n 2, ch 2, planar int16 [L0, L1, R0, R1] = [1, -2, 3, -4]
req2 = struct.pack("<III", 1, 2, 2) + np.array([1, -2, 3, -4], dtype="<i2").tobytes()
assert req2 == bytes([1, 0, 0, 0, 2, 0, 0, 0, 2, 0, 0, 0, 1, 0, 0xFE, 0xFF, 3, 0, 0xFC, 0xFF]), req2.hex()
print("PROTOCOL GOLDEN BYTES OK")
