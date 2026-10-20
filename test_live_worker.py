import unittest
from live_worker import locked_language, trim_overlap, needs_fragment_cleanup, Worker, better_decode, repair_vocabulary, quick_cleanup
from types import SimpleNamespace

class BilingualTests(unittest.TestCase):
    def test_live_cleanup_fast_path(self):
        self.assertFalse(needs_fragment_cleanup("Please send the latest report tomorrow"))
        for text in ("I, I want to um send it", "Kirim eh laporannya", "Friday, no wait Thursday", "Saya saya ingin mengirim"):
            self.assertTrue(needs_fragment_cleanup(text), text)

    def test_only_explicit_languages(self):
        self.assertEqual(locked_language("en"), "en")
        self.assertEqual(locked_language("id"), "id")
        for old in (None, "auto2", "fr", "ms"):
            self.assertEqual(locked_language(old), "en")

    def test_worker_never_autodetects_or_switches(self):
        calls = []
        segment = SimpleNamespace(text="Kirim laporan besok.", avg_logprob=-.1, tokens=[1,2,3],
            words=[SimpleNamespace(word=" Kirim", start=0, end=.3, probability=.9),
                   SimpleNamespace(word=" laporan", start=.3, end=.6, probability=.9),
                   SimpleNamespace(word=" besok.", start=.6, end=1., probability=.9)])
        def transcribe(audio, **options):
            calls.append(options)
            return iter([segment]), SimpleNamespace(language="en")
        worker = Worker()
        worker.prepare = lambda: None
        worker.engine.whisper = SimpleNamespace(transcribe=transcribe)
        result = worker.process({"command":"recognize", "id":1, "samples":[1000]*32000,
            "language":"id", "final":True, "cleanup":False, "vocabulary":"Senko, Jakarta"})
        self.assertEqual(result["language"], "id")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["language"], "id")
        self.assertEqual(calls[0]["hotwords"], "Senko, Jakarta")

    def test_audio_recheck_preserves_negation_and_numbers(self):
        def segment(text, score):
            return [SimpleNamespace(text=text, avg_logprob=score, tokens=[1,2,3])]
        self.assertTrue(better_decode(segment("Please send the re port to the team tomorrow", -.9), segment("Please send the report to the team tomorrow", -.3)))
        self.assertFalse(better_decode(segment("Do not send 3", -.9), segment("Do send 3", -.3)))
        self.assertFalse(better_decode(segment("Send 3", -.9), segment("Send 5", -.3)))

    def test_cleanup_warms_once_per_worker(self):
        warmed = []
        worker = Worker()
        worker.prepare = lambda: None
        worker.engine._cleanup = lambda *args: warmed.append(args)
        for _ in range(2):
            worker.process({"command":"prepare", "cleanup":True})
        self.assertEqual(len(warmed), 0)

    def test_startup_cleanup_preserves_meaning_and_emphasis(self):
        self.assertEqual(quick_cleanup("I, I would like to, um, send the report."), "I would like to send the report.")
        self.assertEqual(quick_cleanup("Saya saya tidak mau kirim."), "Saya tidak mau kirim.")
        self.assertEqual(quick_cleanup("This is very, very important."), "This is very, very important.")

    def test_speech_runtime_releases_model_without_a_rewriting_server(self):
        import weakref
        class Model: pass
        worker = Worker()
        model = Model()
        reference = weakref.ref(model)
        worker.engine.whisper = model
        del model
        self.assertIsNotNone(reference())
        worker.engine.close()
        self.assertIsNone(reference())
        self.assertFalse(hasattr(worker.engine, "server"))

    def test_unstable_tail_is_not_inserted(self):
        worker = Worker()
        worker.prepare = lambda: None
        texts = iter(["The meeting is Friday", "The meeting is Thursday", "The meeting is Thursday morning"])
        def decode(audio, **options):
            text = next(texts)
            words = [SimpleNamespace(word=" "+part, start=3.5+i*.01, end=3.7+i*.01, probability=.9) for i,part in enumerate(text.split())]
            return iter([SimpleNamespace(text=text, words=words)]), None
        worker.engine.whisper = SimpleNamespace(transcribe=decode)
        pieces = []
        for final in (False,False,True):
            result = worker.process({"command":"recognize","id":1,"samples":[1000]*64000,"language":"en","cleanup":False,"final":final})
            pieces.append(result["raw"])
            if not final:
                self.assertEqual(result["consumed"],0)
        self.assertNotIn("Friday"," ".join(pieces))
        self.assertEqual(" ".join(part for part in pieces if part),"The meeting is Thursday morning")

    def test_personal_vocabulary_repairs_uncertain_word_only(self):
        raw = "Check the Kubebutternets deployment."
        word = SimpleNamespace(word=" Kubebutternets", probability=.2)
        self.assertEqual(repair_vocabulary(raw, [word], "Kubernetes, Senko"), "Check the Kubernetes deployment.")
        word.probability = .95
        self.assertEqual(repair_vocabulary(raw, [word], "Kubernetes"), raw)
        word.probability = .2
        self.assertEqual(repair_vocabulary(raw, [word], ""), raw)

    def test_chunk_boundary_does_not_repeat_word(self):
        words = [SimpleNamespace(word=" Thursday.", start=0), SimpleNamespace(word=" This", start=.5)]
        self.assertEqual([w.word for w in trim_overlap(words, "meeting on Thursday.")], [" This"])

    def test_later_deliberate_repetition_is_kept(self):
        words = [SimpleNamespace(word=" very", start=.6)]
        self.assertEqual(trim_overlap(words, "very"), words)

if __name__ == "__main__":
    unittest.main()
