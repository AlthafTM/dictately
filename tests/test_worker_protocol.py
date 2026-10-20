from _support import ROOT
from io import BytesIO
import json
import unittest
import numpy as np
from worker_protocol import read_request, MAX_SAMPLES


def frame(samples):
    header = {"command": "recognize", "audio_format": "s16le", "audio_samples": len(samples)}
    return json.dumps(header).encode() + b"\n" + np.array(samples, dtype="<i2").tobytes()


class ProtocolTests(unittest.TestCase):
    def test_audio_boundaries_and_following_control_frame(self):
        # Includes newline bytes inside PCM, which must not terminate the payload.
        samples = [-32768, -1, 0, 10, 2570, 32767]
        stream = BytesIO(frame(samples) + b'{"command":"prepare"}\n' + frame([]))
        np.testing.assert_array_equal(read_request(stream)["samples"], samples)
        self.assertEqual(read_request(stream), {"command": "prepare"})
        self.assertEqual(len(read_request(stream)["samples"]), 0)
        self.assertIsNone(read_request(stream))

    def test_partial_pipe_reads(self):
        class PartialPipe(BytesIO):
            def readinto(self, view):
                return super().readinto(view[:3])
        np.testing.assert_array_equal(read_request(PartialPipe(frame([1, 2, 3, 4])))["samples"], [1, 2, 3, 4])

    def test_malformed_frames_fail_instead_of_decoding_wrong_audio(self):
        for count in (-1, MAX_SAMPLES + 1, True, 1.5, None):
            stream = BytesIO(json.dumps({"audio_format": "s16le", "audio_samples": count}).encode() + b"\n")
            with self.assertRaises(ValueError):
                read_request(stream)
        with self.assertRaises(EOFError):
            read_request(BytesIO(frame([1, 2])[:-1]))
        with self.assertRaises(ValueError):
            read_request(BytesIO(b'{"command":"prepare"}'))

    def test_legacy_json_samples_still_work(self):
        self.assertEqual(read_request(BytesIO(b'{"samples":[-1,0,1]}\n'))["samples"], [-1, 0, 1])


if __name__ == "__main__":
    unittest.main()
