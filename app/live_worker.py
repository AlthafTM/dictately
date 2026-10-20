"""On-demand offline inference worker for the native Rust controller.

Only the main thread touches inference. stdin uses JSON headers plus PCM frames;
stdout uses JSON lines. Diagnostics go to stderr. This process never captures
the microphone or inserts keystrokes.
"""
from __future__ import annotations
import json
import os
import re
from difflib import SequenceMatcher
from pathlib import Path
import sys
import time

from runtime_paths import app_root, configure_gpu_libraries
configure_gpu_libraries()

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stdin is not None:
    sys.stdin.reconfigure(encoding="utf-8")
from speech_models import selected_model
from worker_protocol import read_request
from language_commands import BilingualBackend, language_command, shared_encoder

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["DO_NOT_TRACK"] = "1"

LANGUAGES = {"en", "id", "both"}

def needs_fragment_cleanup(text):
    """Keep clean ongoing speech on the fast path; edit audible disfluencies."""
    return bool(re.search(r"\b(?:um|uh|erm|hmm|umm|eh|eee|anu)\b|\b(?P<word>\w+)[\s,]+(?P=word)\b|\b(?:i mean|no wait|sorry|maksud saya|maksudnya|bukan|ralat)\b", text, re.IGNORECASE))

def quick_cleanup(text):
    """Conservative startup fallback while the generative editor warms in parallel."""
    text = re.sub(r",?\s*\b(?:um+|uh+|erm|hmm|eh)\b[,\s]*", " ", text, flags=re.IGNORECASE)
    for _ in range(3):
        text = re.sub(r"\b(i|saya|aku|kami|kita|you|we|they)[,\s]+\1\b", r"\1", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" ,")

def dictation_punctuation(text):
    """Omit sentence/session-ending full stops, keeping dots inside words/numbers."""
    return re.sub(r"\.+(?=[\"'\u201d\u2019)\]]*(?:\s|$))", "", text)

def trim_overlap(words, previous):
    if not words or not previous or words[0].start > .25:
        return words
    normalize = lambda value: re.sub(r"[^\w]", "", value.lower())
    before = [normalize(word) for word in previous.split()][-6:]
    after = [normalize(word.word) for word in words[:6]]
    for size in range(min(len(before), len(after)), 0, -1):
        if all(before[-size:]) and before[-size:] == after[:size]:
            return words[size:]
    return words

def locked_language(value):
    # Legacy automatic settings migrate to English. Never ask the model to detect.
    return value if value in LANGUAGES else "en"

def uncertain(segments):
    words = [w for s in segments for w in (s.words or []) if re.search(r"\w", w.word)]
    return any(s.avg_logprob < -.65 for s in segments) or any(w.probability < .50 for w in words)

def vocabulary_terms(vocabulary):
    return [term.strip() for term in re.split(r"[,;\n]", vocabulary)[:40] if term.strip().isalpha() and 4 <= len(term.strip()) <= 40]

def vocabulary_fit(text, term):
    words = re.findall(r"[a-zA-Z]+", text.lower())
    return max((SequenceMatcher(None, "".join(words[i:i+n]), term.lower()).ratio()
        for i in range(len(words)) for n in (1,2)), default=0.)

def repair_vocabulary(raw, words, vocabulary):
    terms = vocabulary_terms(vocabulary)
    result = raw
    for word in words:
        source = word.word.strip().strip(".,!?;:'\"")
        if word.probability >= .65 or not source.isalpha() or len(source) < 4:
            continue
        scores = sorted([(SequenceMatcher(None, source.lower(), term.lower()).ratio(), term) for term in terms], reverse=True)
        if not scores:
            continue
        threshold = .72 if len(source) >= 8 else .84
        if scores[0][0] < threshold or (len(scores) > 1 and scores[0][0] - scores[1][0] < .08):
            continue
        result = re.sub(r"\b" + re.escape(source) + r"\b", lambda _: scores[0][1], result, count=1)
    return result

def better_decode(original, alternative, vocabulary=""):
    """Accept an acoustically better hypothesis without obvious semantic loss."""
    before = " ".join(s.text for s in original).strip()
    after = " ".join(s.text for s in alternative).strip()
    if not before or not after or not .85 <= len(after.split()) / len(before.split()) <= 1.15:
        return False
    if re.findall(r"\d+", before) != re.findall(r"\d+", after):
        return False
    negation = lambda text: set(re.findall(r"\b(?:not|never|no|tidak|jangan|bukan)\b|n't\b", text.lower()))
    if negation(before) != negation(after):
        return False
    for term in vocabulary_terms(vocabulary):
        prior = vocabulary_fit(before, term)
        if prior >= .72 and vocabulary_fit(after, term) < prior - .05:
            return False
    score = lambda parts: sum(s.avg_logprob * max(1, len(s.tokens)) for s in parts) / max(1, sum(len(s.tokens) for s in parts))
    return score(alternative) > score(original) + .025

def emit(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)

class SpeechRuntime:
    """Own only the speech model; live dictation has no rewriting server."""
    def __init__(self):
        self.whisper = None

    def close(self):
        self.whisper = None
        import gc
        gc.collect()


class Worker:
    def __init__(self, model_root=None):
        self.engine = SpeechRuntime()
        self.model_root = Path(model_root) if model_root is not None else app_root()
        self.device = "cpu"
        self.force_cpu = False
        self.model_name = "unloaded"
        self.committed_words = []
        self.previous_words = []

    def prepare(self):
        if self.engine.whisper is None:
            from faster_whisper import WhisperModel
            import ctranslate2
            import numpy as np
            model_dir = selected_model(self.model_root)
            if os.environ.get("LOCALFLOW_MODEL") == "small":
                model_dir = "whisper-small"
            model_path = str(self.model_root / "models" / model_dir)
            self.model_name = model_dir
            if not self.force_cpu and os.environ.get("LOCALFLOW_DEVICE") != "cpu" and ctranslate2.get_cuda_device_count() > 0:
                try:
                    self.engine.whisper = WhisperModel(model_path, device="cuda", compute_type="int8_float16",
                        cpu_threads=4, num_workers=1, local_files_only=True,
                        flash_attention=os.environ.get("TRANSCRIBER_FLASH", "0") == "1")
                    self.engine.whisper.detect_language(audio=np.zeros(16000, dtype=np.float32))
                    self.device = "cuda"
                except Exception as exc:
                    print(f"Retrying GPU without flash attention: {exc}", file=sys.stderr, flush=True)
                    try:
                        self.engine.whisper = WhisperModel(model_path, device="cuda", compute_type="int8_float16",
                            cpu_threads=4, num_workers=1, local_files_only=True)
                        self.engine.whisper.detect_language(audio=np.zeros(16000, dtype=np.float32))
                        self.device = "cuda"
                    except Exception as fallback:
                        print(f"GPU unavailable; CPU fallback: {fallback}", file=sys.stderr, flush=True)
                        self.engine.whisper = None
            if self.engine.whisper is None:
                self.device = "cpu"
                self.engine.whisper = WhisperModel(model_path, device="cpu", compute_type="int8",
                    cpu_threads=4, num_workers=1, local_files_only=True)
            self.engine.whisper.model = BilingualBackend(self.engine.whisper.model)

    def process(self, request):
        import numpy as np
        started = time.monotonic()
        if request["command"] == "prepare":
            self.committed_words = []
            self.previous_words = []
            # Recognition supplies punctuation. Never warm a separate rewriting model.
            self.prepare()
            return {"type": "ready", "id": request.get("id", 0), "device": self.device, "model":self.model_name}
        audio = np.array(request["samples"], dtype=np.float32, copy=True)
        audio *= 1.0 / 32768.0
        if request['command']=='select_language':
            if len(audio)<6400 or float(np.sqrt(np.dot(audio,audio)/len(audio)))<.001:
                return {'type':'language_selected','id':request.get('id',0),'language':None,'consumed':len(audio)}
            self.prepare()
            choices=[]
            with shared_encoder(self.engine.whisper):
                for language in ('en','id'):
                    segments,_=self.engine.whisper.transcribe(audio,language=language,beam_size=3,
                        temperature=0.0,condition_on_previous_text=False,vad_filter=True)
                    choice=language_command(' '.join(segment.text for segment in segments))
                    if choice:choices.append(choice)
            # A spoken "both" wins, otherwise conflicting decodes require a retry.
            selected='both' if 'both' in choices else choices[0] if choices and len(set(choices))==1 else None
            return {'type':'language_selected','id':request.get('id',0),'language':selected,'consumed':len(audio)}
        final = request.get("final", False)
        if len(audio) < 3200 or float(np.sqrt(np.dot(audio, audio) / len(audio))) < .0005:
            return {"type": "result", "id": request["id"], "raw": "", "cleaned": "", "preview": "",
                "consumed": len(audio) if final else max(0, len(audio)-4800), "language": locked_language(request.get("language")), "seconds": 0}
        self.prepare()
        model = self.engine.whisper
        selected = locked_language(request.get("language"))
        vocabulary = str(request.get("vocabulary") or "")[:600].strip()
        options = dict(task="transcribe", beam_size=5 if request.get("accuracy", True) else 1, temperature=0.0, word_timestamps=True, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 300}, condition_on_previous_text=False,
            hotwords=vocabulary or None,
            compression_ratio_threshold=1.9, log_prob_threshold=-.8, hallucination_silence_threshold=1.0)
        if selected=='both':
            # The underlying detector is filtered by BilingualBackend, including
            # detection performed by faster-whisper inside multilingual segments.
            segments, info = model.transcribe(audio, language=None, multilingual=True, **options)
            detected=getattr(info,'language','en')
        else:
            segments, info = model.transcribe(audio, language=selected, **options)
            detected=selected
        segments = list(segments)
        preview = " ".join(seg.text.strip() for seg in segments).strip()
        words = [word for seg in segments for word in (seg.words or [])]
        normalize = lambda word: re.sub(r"[^\w]", "", word.lower())
        current = [normalize(word.word) for word in words]
        trailing_silence = len(audio)/16000 - (words[-1].end if words else len(audio)/16000)
        flush = bool(final or request.get("flush", False) or trailing_silence >= .8 or len(audio) >= 18*16000)
        common = 0
        for before, after in zip(self.previous_words, current):
            if before != after:
                break
            common += 1
        stable = len(words) if flush else min(common, max(0, len(words)-2))
        start = len(self.committed_words)
        if current[:start] != self.committed_words:
            # Word times drift across redecodes. Align text, never chop audio by a word time.
            for block in SequenceMatcher(None, self.committed_words, current, autojunk=False).get_matching_blocks():
                if block.size and block.a+block.size == len(self.committed_words):
                    start = block.b+block.size
        chosen = words[start:stable]
        raw = "".join(word.word for word in chosen).strip()
        if not words and flush and not self.committed_words:
            raw = preview
        self.previous_words = current
        if stable > start:
            self.committed_words = current[:stable]
        consumed = len(audio) if flush else 0
        if flush:
            self.committed_words = []
            self.previous_words = []
        cleaned = quick_cleanup(raw) if request.get("cleanup", True) else raw
        applied = cleaned != raw
        cleaned = dictation_punctuation(re.sub(r"\s+", " ", cleaned).strip())
        self.engine.last_used = time.monotonic()
        return {"type": "result", "id": request["id"], "raw": raw, "cleaned": cleaned,
            "preview": preview, "consumed": consumed, "language": detected if detected in ('en','id') else 'en',
            "through": 0, "flushed":flush,
            "cleanup_applied": applied, "accuracy_retry": False, "seconds": round(time.monotonic() - started, 3)}

def main():
    worker = Worker()
    try:
        while True:
            request = read_request(sys.stdin.buffer)
            if request is None:
                break
            try:
                if request.get("command") == "quit":
                    break
                emit(worker.process(request))
            except Exception as exc:
                import traceback
                traceback.print_exc(file=sys.stderr)
                if worker.device == "cuda":
                    # Retry the same audio on CPU if a game or another app fills VRAM.
                    worker.engine.whisper = None
                    worker.force_cpu = True
                    try:
                        emit(worker.process(request))
                        continue
                    except Exception as retry:
                        exc = retry
                        traceback.print_exc(file=sys.stderr)
                emit({"type": "error", "id": request.get("id", 0), "error": str(exc)})
    finally:
        worker.engine.close()

if __name__ == "__main__":
    main()
