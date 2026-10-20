"""Small JSON control headers followed by exact signed 16-bit PCM payloads."""
import json

MAX_SAMPLES = 16000 * 150
MAX_HEADER = 16 * 1024 * 1024  # Also accept legacy JSON sample requests for tooling.


def read_request(stream):
    line = stream.readline(MAX_HEADER + 1)
    if not line:
        return None
    if len(line) > MAX_HEADER or not line.endswith(b"\n"):
        raise ValueError("Invalid worker header")
    request = json.loads(line)
    if "audio_format" in request:
        count = request.get("audio_samples")
        if request["audio_format"] != "s16le" or type(count) is not int or not 0 <= count <= MAX_SAMPLES:
            raise ValueError("Invalid PCM frame")
        remaining = count * 2
        payload = bytearray(remaining)
        view = memoryview(payload)
        offset = 0
        while offset < remaining:
            received = stream.readinto(view[offset:])
            if not received:
                raise EOFError("Truncated PCM frame")
            offset += received
        import numpy as np
        request["samples"] = np.frombuffer(payload, dtype="<i2")
    return request
