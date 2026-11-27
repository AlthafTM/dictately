from _support import ROOT
from datetime import datetime
from types import SimpleNamespace
import unittest
import numpy as np

from text_processing import (
    has_speech_energy,
    filter_segments,
    parse_vocabulary_terms,
    cap_vocabulary_prompt,
    build_initial_prompt,
    filter_dictionary_echo,
    apply_replacements,
    expand_snippets,
    normalize_indonesian_reduplication,
    normalize_formal_indonesian,
    clean_dictation,
)
from live_worker import Worker, repair_vocabulary
from language_commands import language_command
from screen_capture import screenshot_command
from settings_window import parse_mapping_lines, format_mapping_lines


class IndonesianAndPipelineTests(unittest.TestCase):
    def test_speech_gate_rejects_room_hiss_and_keeps_speech(self):
        hiss = np.full(16000, 0.0008, dtype=np.float32)
        self.assertFalse(has_speech_energy(hiss))
        short_clip = np.full(1000, 0.1, dtype=np.float32)
        self.assertFalse(has_speech_energy(short_clip))
        speech = np.zeros(16000, dtype=np.float32)
        speech[4000:4640] = 0.05
        self.assertTrue(has_speech_energy(speech))

    def test_no_speech_and_indonesian_hallucinations_are_filtered(self):
        segs = [
            SimpleNamespace(text="Terima kasih telah menonton.", no_speech_prob=0.5, avg_logprob=-0.8),
            SimpleNamespace(text="Tolong kirim laporan besok pagi.", no_speech_prob=0.05, avg_logprob=-0.2),
            SimpleNamespace(text="Random ghost tail", no_speech_prob=0.75, avg_logprob=-1.2),
            SimpleNamespace(text="Quiet real speech", no_speech_prob=0.75, avg_logprob=-0.4),
            SimpleNamespace(text="Terima kasih sudah membantu saya.", no_speech_prob=0.1, avg_logprob=-0.2),
        ]
        kept = [s.text for s in filter_segments(segs)]
        self.assertEqual(kept, ["Tolong kirim laporan besok pagi.", "Quiet real speech", "Terima kasih sudah membantu saya."])

    def test_dictionary_echo_discard_and_trailing_strip(self):
        prompt = "Senko, Jakarta, Telkom University, GoPay"
        terms = ["Senko", "Jakarta", "Telkom University", "GoPay"]
        self.assertEqual(filter_dictionary_echo("Senko, Jakarta, Telkom University, GoPay.", prompt, terms), "")
        self.assertEqual(
            filter_dictionary_echo("Kirim laporan besok pagi. Senko, Jakarta.", prompt, terms),
            "Kirim laporan besok pagi.",
        )
        # Ordinary dictation using a single dictionary term must survive.
        self.assertEqual(filter_dictionary_echo("Saya tinggal di Jakarta.", prompt, terms), "Saya tinggal di Jakarta.")

    def test_vocabulary_preserves_indonesian_acronyms_symbols_and_cap(self):
        terms = parse_vocabulary_terms("KTP, QRIS, GoPay, Node.js, C#, Wi-Fi, Telkom University, a")
        self.assertEqual(terms, ["KTP", "QRIS", "GoPay", "Node.js", "C#", "Wi-Fi", "Telkom University"])
        prompt = cap_vocabulary_prompt("GoPay, QRIS", snippets=[{"trigger": "salam hormat", "replacement": "Hormat saya"}], max_chars=25)
        self.assertEqual(prompt, "GoPay, QRIS, salam hormat")
        word = SimpleNamespace(word=" GoPey", probability=0.2)
        self.assertEqual(repair_vocabulary("Bayar pakai GoPey ya", [word], "GoPay, QRIS"), "Bayar pakai GoPay ya")

    def test_indonesian_cleanup_protects_reduplication_and_collapses_stutters(self):
        self.assertEqual(
            clean_dictation("Saya, saya mau ehm pergi pelan pelan ya", language="id"),
            "Saya mau pergi pelan-pelan ya",
        )
        self.assertEqual(
            clean_dictation("Teman teman tolong hati hati di jalan", language="id"),
            "Teman-teman tolong hati-hati di jalan",
        )
        self.assertEqual(
            clean_dictation("gue gue anu mau kirim besok", language="id"),
            "gue mau kirim besok",
        )
        self.assertEqual(normalize_indonesian_reduplication("Tiba tiba hujan turun"), "Tiba-tiba hujan turun")

    def test_formal_indonesian_baku_normalization_is_opt_in_and_preserves_case(self):
        casual = "Gak udah gimana dong, soalnya blm ada kabar, makasih ya"
        self.assertEqual(clean_dictation(casual, language="id", formal_indonesian=False), casual)
        self.assertEqual(
            normalize_formal_indonesian(casual),
            "Tidak sudah bagaimana dong, karena belum ada kabar, terima kasih ya",
        )

    def test_replacements_and_snippets_with_indonesian_dates(self):
        now = datetime(2026, 11, 27, 9, 5)
        replaced = apply_replacements("Kirim ke kantor jaksel sekarang", [{"source": "jaksel", "target": "Jakarta Selatan"}])
        self.assertEqual(replaced, "Kirim ke kantor Jakarta Selatan sekarang")

        snippets = [
            {"trigger": "salam hormat", "replacement": "Hormat saya, Althaf ({{tanggal}} {{waktu}})"},
            {"trigger": "hari ini", "replacement": "Tanggal {{date}}"},
        ]
        self.assertEqual(
            expand_snippets("Terima kasih salam hormat", snippets, language="id", now=now),
            "Terima kasih Hormat saya, Althaf (27 November 2026 09.05)",
        )
        self.assertEqual(
            expand_snippets("Update hari ini selesai", snippets, language="id", now=now),
            "Update Tanggal 27 November 2026 selesai",
        )
        self.assertEqual(
            expand_snippets("Update hari ini selesai", snippets, language="en", now=now),
            "Update Tanggal November 27, 2026 selesai",
        )

    def test_worker_end_to_end_indonesian_features_and_lowercase_model_capitalization(self):
        calls = []
        seg = SimpleNamespace(
            text="saya saya gak jadi ke kantor jaksel salam hormat",
            no_speech_prob=0.05,
            avg_logprob=-0.15,
            tokens=[1, 2, 3],
            words=[
                SimpleNamespace(word=" " + w, start=i * 0.2, end=(i + 1) * 0.2, probability=0.95)
                for i, w in enumerate("saya saya gak jadi ke kantor jaksel salam hormat".split())
            ],
        )

        def transcribe(audio, **options):
            calls.append(options)
            return iter([seg]), SimpleNamespace(language="id")

        worker = Worker()
        worker.prepare = lambda language=None: None
        worker.model_name = "whisper-turbo-id"
        worker.engine.whisper = SimpleNamespace(transcribe=transcribe)
        result = worker.process({
            "command": "recognize",
            "id": 1,
            "samples": [1500] * 32000,
            "language": "id",
            "final": True,
            "cleanup": True,
            "formal_indonesian": True,
            "vocabulary": "GoPay, QRIS",
            "replacements": [{"source": "jaksel", "target": "Jakarta Selatan"}],
            "snippets": [{"trigger": "salam hormat", "replacement": "Hormat saya"}],
        })
        self.assertEqual(result["cleaned"], "Saya tidak jadi ke kantor Jakarta Selatan Hormat saya")
        self.assertIn("salam hormat", calls[0]["hotwords"])
        self.assertIn("GoPay", calls[0]["hotwords"])

    def test_expanded_indonesian_voice_commands(self):
        for phrase, expected in [
            ("indo", "id"),
            ("pakai bahasa indonesia", "id"),
            ("pindah ke inggris", "en"),
            ("ganti ke indo", "id"),
        ]:
            self.assertEqual(language_command(phrase), expected, phrase)
        for phrase in ("tangkapan layar", "tangkap layar", "skrinsot", "skrinshot"):
            self.assertTrue(screenshot_command(phrase), phrase)

    def test_settings_mapping_parser_roundtrip(self):
        raw = "# comment\njaksel => Jakarta Selatan\nsalam => Hormat saya\\nAlthaf\ninvalid line\n"
        parsed = parse_mapping_lines(raw, "trigger", "replacement")
        self.assertEqual(parsed, [
            {"trigger": "jaksel", "replacement": "Jakarta Selatan"},
            {"trigger": "salam", "replacement": "Hormat saya\nAlthaf"},
        ])
        self.assertEqual(format_mapping_lines(parsed, "trigger", "replacement"), "jaksel => Jakarta Selatan\nsalam => Hormat saya\\nAlthaf")

    def test_initial_prompt_decoding_freedom_and_multiword_vocab_repair(self):
        prompt = build_initial_prompt("Sebelumnya kita bahas jadwal rapat", "Telkom University, GoPay")
        self.assertIn("jadwal rapat", prompt)
        self.assertIn("Telkom University", prompt)
        self.assertEqual(
            repair_vocabulary("Saya kuliah di telkom universiti hari ini", [], "Telkom University, GoPay"),
            "Saya kuliah di Telkom University hari ini",
        )
        # Wider uncertainty repair (< 0.85) fixes moderately uncertain domain words too.
        word = SimpleNamespace(word=" GoPey", probability=0.72)
        self.assertEqual(repair_vocabulary("Bayar pakai GoPey", [word], "GoPay, QRIS"), "Bayar pakai GoPay")

        calls = []
        seg = SimpleNamespace(
            text="Tolong cek Telkom University ya",
            no_speech_prob=0.05,
            avg_logprob=-0.2,
            tokens=[1, 2, 3],
            words=[SimpleNamespace(word=" Tolong", start=0.0, end=0.3, probability=0.95)],
        )

        def transcribe(audio, **options):
            calls.append(options)
            return iter([seg]), SimpleNamespace(language="id")

        worker = Worker()
        worker.prepare = lambda language=None: None
        worker.model_name = "whisper-turbo-id"
        worker.engine.whisper = SimpleNamespace(transcribe=transcribe)
        worker.process({
            "command": "recognize",
            "id": 2,
            "samples": [1500] * 32000,
            "language": "id",
            "final": True,
            "accuracy": True,
            "vocabulary": "Telkom University, GoPay",
            "previous_text": "Kemarin kita ketemu di kampus",
        })
        self.assertEqual(calls[0]["temperature"], (0.0, 0.2, 0.4, 0.6))
        self.assertEqual(calls[0]["best_of"], 5)
        self.assertAlmostEqual(calls[0]["patience"], 1.5)
        self.assertAlmostEqual(calls[0]["log_prob_threshold"], -1.25)
        self.assertIn("ketemu di kampus", calls[0]["initial_prompt"])
        self.assertIn("Telkom University", calls[0]["initial_prompt"])

    def test_cuda_fallback_resets_after_cooldown(self):
        import time
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            worker = Worker(model_root=tmp)
            worker.force_cpu = True
            worker.cpu_fallback_at = time.monotonic() - 125.0
            worker.engine.whisper = SimpleNamespace(close=lambda: None)
            worker.model_name = "whisper-turbo"
            try:
                worker.prepare(language="en")
            except Exception:
                pass
            self.assertFalse(worker.force_cpu)
            self.assertIsNone(worker.cpu_fallback_at)


if __name__ == "__main__":
    unittest.main()
