import base64
import importlib
import io
import sys
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "loadtest"))
harness = importlib.import_module("controlled_benchmark")
analysis = importlib.import_module("analyze_controlled")


class ControlledBenchmarkTests(unittest.TestCase):
    def test_statistics_missing_values_and_reference_errors(self):
        self.assertEqual(analysis.percentile([None, 4, 1, 3, 2], .5), 2)
        self.assertEqual(analysis.percentile([None, 4, 1, 3, 2], .95), 4)
        self.assertIsNone(analysis.percentile([None], .5))
        self.assertEqual(analysis.normalize("Hello, WORLD!"), "hello world")
        self.assertEqual(analysis.distance(["a", "b"], ["a", "c"]), 1)
        self.assertEqual(analysis.distance(["a", "b"], []), 2)
        low, high = analysis.wilson(10, 10)
        self.assertGreater(low, .7)
        self.assertAlmostEqual(high, 1)

    def test_plan_is_balanced_paired_and_deterministic(self):
        plan = harness.make_plan()
        self.assertEqual(plan, harness.make_plan())
        self.assertEqual(len(plan["dataset"]), 30)
        self.assertEqual(len(plan["trials"]), 90)
        self.assertEqual(plan["expected_input_runs"], 180)
        self.assertEqual(plan["expected_stable_listener_input_results"], 540)
        for category in ("en", "hi", "ta", "te", "mix"):
            self.assertEqual(sum(d["category"] == category for d in plan["dataset"]), 6)
        grouped = {}
        for trial in plan["trials"]:
            grouped.setdefault((trial["pair"], trial["repetition"]), []).append(trial)
        for pair in grouped.values():
            self.assertEqual({t["condition"] for t in pair}, {"alternating", "overlap"})
            self.assertEqual(pair[0]["inputs"], pair[1]["inputs"])
            self.assertEqual(pair[0]["targets"], pair[1]["targets"])

    def test_wav_validation_checks_real_samples(self):
        stream = io.BytesIO()
        with wave.open(stream, "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\x01\x01" * 1600)
        raw, stats = harness.validate_wav(base64.b64encode(stream.getvalue()).decode())
        self.assertTrue(stats["wav_valid"])
        self.assertEqual(stats["wav_duration_s"], .1)
        self.assertEqual(stats["wav_sha256"], harness.digest(raw))
        with self.assertRaises((ValueError, wave.Error, EOFError)):
            harness.validate_wav("AA==")
