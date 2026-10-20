"""Repeatable transport microbenchmark, not an ASR or end-to-end latency test."""
from io import BytesIO
import json
from pathlib import Path
import statistics
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from worker_protocol import read_request


def milliseconds(operation):
    for _ in range(3):
        operation()
    times = []
    for _ in range(25):
        start = time.perf_counter()
        operation()
        times.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(times), 3)


def main():
    rows = []
    for seconds in (3, 18):
        # Fixed seed; results include quiet and full-scale signed samples.
        audio = np.random.default_rng(42).integers(-32768, 32768, 16000 * seconds, dtype=np.int16)
        legacy = (json.dumps({"command": "recognize", "samples": audio.tolist()}, separators=(",", ":")) + "\n").encode()
        header = json.dumps({"command": "recognize", "audio_format": "s16le", "audio_samples": len(audio)}).encode() + b"\n"
        packed = header + audio.astype("<i2", copy=False).tobytes()
        np.testing.assert_array_equal(read_request(BytesIO(packed))["samples"], audio)
        rows.append({"audio_seconds": seconds, "json_bytes": len(legacy), "pcm_frame_bytes": len(packed),
                     "payload_reduction_percent": round((1-len(packed)/len(legacy))*100, 1),
                     "json_parse_median_ms": milliseconds(lambda: read_request(BytesIO(legacy))),
                     "pcm_parse_median_ms": milliseconds(lambda: read_request(BytesIO(packed)))})
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
