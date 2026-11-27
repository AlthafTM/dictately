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
from speech_models import selected_model, is_lowercase_model
from worker_protocol import read_request
from screen_capture import screenshot_command, screenshot_candidate
from language_commands import language_command, shared_encoder
from text_processing import (
    has_speech_energy,
    filter_segments,
    parse_vocabulary_terms,
    cap_vocabulary_prompt,
    build_initial_prompt,
    filter_dictionary_echo,
    apply_replacements,
    expand_snippets,
    clean_dictation,
)

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["DO_NOT_TRACK"] = "1"

LANGUAGES = {"en", "id"}


def needs_fragment_cleanup(text):
    """Keep clean ongoing speech on the fast path; edit audible disfluencies."""
    return bool(re.search(
        r"\b(?:um|uh|erm|hmm|umm|eh|ehm|emm|eee|anu)\b|\b(?P<word>\w+)[\s,]+(?P=word)\b|\b(?:i mean|no wait|sorry|maksud saya|maksud aku|maksud gue|maksudnya|bukan|ralat|koreksi)\b",
        text,
        re.IGNORECASE,
    ))


def quick_cleanup(text, language="en", formal_indonesian=False):
    """Conservative cleanup tuned for English and Indonesian without altering meaning."""
    return clean_dictation(text, language=language, cleanup=True, formal_indonesian=formal_indonesian)


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
    return parse_vocabulary_terms(vocabulary, limit=60)


def vocabulary_fit(text, term):
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    target = re.sub(r"[^a-zA-Z0-9]+", "", term.lower())
    if not target:
        return 0.0
    return max(
        (SequenceMatcher(None, "".join(words[i:i + n]), target).ratio()
         for i in range(len(words)) for n in (1, 2)),
        default=0.0,
    )


def repair_vocabulary(raw, words, vocabulary):
    terms = vocabulary_terms(vocabulary)
    if not terms or not raw:
        return raw
    single_terms = [t for t in terms if " " not in t]
    multi_terms = [t for t in terms if " " in t]
    result = raw
    for word in words:
        source = word.word.strip().strip(".,!?;:'\"")
        if word.probability >= .85 or len(source) < 3 or not any(ch.isalnum() for ch in source):
            continue
        source_norm = re.sub(r"[^a-zA-Z0-9]", "", source).lower()
        if len(source_norm) < 3:
            continue
        scores = sorted(
            [(SequenceMatcher(None, source_norm, re.sub(r"[^a-zA-Z0-9]", "", term).lower()).ratio(), term)
             for term in single_terms if len(re.sub(r"[^a-zA-Z0-9]", "", term)) >= 3],
            reverse=True,
        )
        if not scores:
            continue
        threshold = .72 if len(source_norm) >= 8 else .80 if len(source_norm) >= 5 else .84
        if scores[0][0] < threshold or (len(scores) > 1 and scores[0][0] - scores[1][0] < .08):
            continue
        result = re.sub(r"(?<!\w)" + re.escape(source) + r"(?!\w)", lambda _: scores[0][1], result, count=1)
    # Also repair closely matching multi-word proper nouns/terms (e.g., "telkom universiti" -> "Telkom University").
    for term in multi_terms:
        part_count = len(term.split())
        tokens = list(re.finditer(r"[^\s,.;:!?]+", result))
        if len(tokens) < part_count:
            continue
        target_norm = re.sub(r"[^a-zA-Z0-9]+", "", term.lower())
        if len(target_norm) < 6:
            continue
        for idx in range(len(tokens) - part_count + 1):
            span_start = tokens[idx].start()
            span_end = tokens[idx + part_count - 1].end()
            candidate = result[span_start:span_end]
            cand_norm = re.sub(r"[^a-zA-Z0-9]+", "", candidate.lower())
            if SequenceMatcher(None, cand_norm, target_norm).ratio() >= 0.84:
                result = result[:span_start] + term + result[span_end:]
                break
    return result


def better_decode(original, alternative, vocabulary=""):
    """Accept an acoustically better hypothesis without obvious semantic loss."""
    before = " ".join(s.text for s in original).strip()
    after = " ".join(s.text for s in alternative).strip()
    if not before or not after or not .85 <= len(after.split()) / len(before.split()) <= 1.15:
        return False
    if re.findall(r"\d+", before) != re.findall(r"\d+", after):
        return False
    negation = lambda text: set(re.findall(r"\b(?:not|never|no|tidak|jangan|bukan|belum|tanpa)\b|n't\b", text.lower()))
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
        self.cpu_fallback_at = None
        self.model_name = "unloaded"
        self.committed_words = []
        self.previous_words = []
        self.session_dictated = False

    def prepare(self, language=None):
        model_dir = selected_model(self.model_root, language=language)
        if os.environ.get("LOCALFLOW_MODEL") == "small":
            model_dir = "whisper-small"
        # Retry CUDA automatically after a transient VRAM/sleep fallback cooldown.
        if self.force_cpu and self.cpu_fallback_at is not None and (time.monotonic() - self.cpu_fallback_at) >= 120.0:
            self.force_cpu = False
            self.cpu_fallback_at = None
            if self.engine.whisper is not None:
                self.engine.close()
        if self.engine.whisper is not None and self.model_name == model_dir:
            return
        if self.engine.whisper is not None:
            self.engine.close()
        from faster_whisper import WhisperModel
        import ctranslate2
        import numpy as np
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

    def _prepare_for_language(self, language):
        try:
            self.prepare(language=language)
        except TypeError:
            # Support test stubs replacing worker.prepare with a zero-argument callable.
            self.prepare()

    def process(self, request):
        import numpy as np
        started = time.monotonic()
        if request["command"] == "prepare":
            self.committed_words = []
            self.previous_words = []
            self.session_dictated = False
            # Recognition supplies punctuation. Never warm a separate rewriting model.
            req_lang = request.get("language")
            self._prepare_for_language(locked_language(req_lang) if req_lang in LANGUAGES else None)
            return {"type": "ready", "id": request.get("id", 0), "device": self.device, "model": self.model_name}
        audio = np.array(request["samples"], dtype=np.float32, copy=True)
        audio *= 1.0 / 32768.0
        if request["command"] == "select_language":
            if len(audio) < 6400 or not has_speech_energy(audio):
                return {"type": "language_selected", "id": request.get("id", 0), "language": None, "consumed": len(audio)}
            first = locked_language(request.get("language"))
            self._prepare_for_language(first)
            selected = None
            with shared_encoder(self.engine.whisper):
                for language in (first, "id" if first == "en" else "en"):
                    segments, _ = self.engine.whisper.transcribe(audio, language=language, beam_size=1,
                        temperature=0.0, condition_on_previous_text=False, vad_filter=True)
                    selected = language_command(" ".join(segment.text for segment in segments))
                    if selected:
                        break
            return {"type": "language_selected", "id": request.get("id", 0), "language": selected, "consumed": len(audio)}
        final = request.get("final", False)
        selected = locked_language(request.get("language"))
        if not has_speech_energy(audio):
            return {"type": "result", "id": request["id"], "raw": "", "cleaned": "", "preview": "",
                "consumed": len(audio) if final else max(0, len(audio) - 4800), "language": selected, "seconds": 0}
        self._prepare_for_language(selected)
        model = self.engine.whisper
        vocabulary = str(request.get("vocabulary") or "")[:600].strip()
        snippets = request.get("snippets") or []
        replacements = request.get("replacements") or []
        hotwords_prompt = cap_vocabulary_prompt(vocabulary, snippets=snippets)
        context_prompt = build_initial_prompt(request.get("previous_text", ""), hotwords_prompt)
        accurate = bool(request.get("accuracy", True))
        options = dict(
            task="transcribe",
            beam_size=5 if accurate else 2,
            best_of=5 if accurate else 2,
            patience=1.5 if accurate else 1.0,
            temperature=(0.0, 0.2, 0.4, 0.6),
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 350, "speech_pad_ms": 200},
            condition_on_previous_text=False,
            initial_prompt=context_prompt,
            hotwords=hotwords_prompt or None,
            compression_ratio_threshold=2.4,
            log_prob_threshold=-1.25,
            no_speech_threshold=.6,
            hallucination_silence_threshold=1.0,
        )
        segments, info = model.transcribe(audio, language=selected, **options)
        detected = selected
        segments = filter_segments(list(segments))
        preview = " ".join(seg.text.strip() for seg in segments).strip()
        words = [word for seg in segments for word in (seg.words or [])]
        # Word timestamps define the pause: microphone noise must not postpone capture.
        # Full-phrase matching still protects ordinary dictation with extra words.
        can_command = not self.session_dictated and not request.get("previous_text", "").strip() and not self.committed_words
        word_ends = [float(word.end) for word in words if any(ch.isalnum() for ch in word.word)]
        word_pause = max(0., len(audio) / 16000. - max(word_ends)) if word_ends else None
        command_ready = bool(final or (word_pause is not None and word_pause >= 0.25) or
                             (word_pause is None and request.get("pause_seconds", 0) >= 0.25))
        if can_command and screenshot_command(preview) and command_ready:
            self.committed_words = []
            self.previous_words = []
            return {"type": "result", "id": request["id"], "raw": "", "cleaned": "", "preview": "",
                    "action": "screenshot", "consumed": len(audio), "through": 0, "flushed": True,
                    "language": selected, "seconds": round(time.monotonic() - started, 3)}
        if can_command and screenshot_candidate(preview) and not command_ready:
            return {"type": "result", "id": request["id"], "raw": "", "cleaned": "", "preview": preview,
                    "consumed": 0, "through": 0, "flushed": False, "language": selected, "seconds": round(time.monotonic() - started, 3)}
        normalize = lambda word: re.sub(r"[^\w]", "", word.lower())
        current = [normalize(word.word) for word in words]
        trailing_silence = len(audio) / 16000 - (words[-1].end if words else len(audio) / 16000)
        flush = bool(final or request.get("flush", False) or trailing_silence >= .55 or len(audio) >= 18 * 16000)
        common = 0
        for before, after in zip(self.previous_words, current):
            if before != after:
                break
            common += 1
        stable = len(words) if flush else min(common, max(0, len(words) - 2))
        start = len(self.committed_words)
        if current[:start] != self.committed_words:
            # Word times drift across redecodes. Align text, never chop audio by a word time.
            for block in SequenceMatcher(None, self.committed_words, current, autojunk=False).get_matching_blocks():
                if block.size and block.a + block.size == len(self.committed_words):
                    start = block.b + block.size
        chosen = words[start:stable]
        raw = "".join(word.word for word in chosen).strip()
        if not words and flush and not self.committed_words:
            raw = preview
        is_phrase_start = not self.committed_words and not request.get("previous_text", "").strip()
        self.previous_words = current
        if stable > start:
            self.committed_words = current[:stable]
        consumed = len(audio) if flush else 0
        if flush:
            self.committed_words = []
            self.previous_words = []

        if raw and hotwords_prompt:
            dict_terms = parse_vocabulary_terms(vocabulary)
            raw = filter_dictionary_echo(raw, hotwords_prompt, dictionary_terms=dict_terms)

        cleaned = raw
        if cleaned and vocabulary and chosen:
            cleaned = repair_vocabulary(cleaned, chosen, vocabulary)
        if cleaned:
            cleaned = clean_dictation(
                cleaned,
                language=selected,
                cleanup=bool(request.get("cleanup", True)),
                formal_indonesian=bool(request.get("formal_indonesian", False)),
                capitalize_lowercase=is_lowercase_model(self.model_name),
                is_phrase_start=is_phrase_start,
            )
            if replacements:
                cleaned = apply_replacements(cleaned, replacements)
            if snippets:
                cleaned = expand_snippets(cleaned, snippets, language=selected)
        applied = cleaned != raw
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = dictation_punctuation(cleaned)
        if raw.strip():
            self.session_dictated = True
        self.engine.last_used = time.monotonic()
        return {"type": "result", "id": request["id"], "raw": raw, "cleaned": cleaned,
            "preview": preview, "consumed": consumed, "language": detected if detected in ("en", "id") else "en",
            "through": 0, "flushed": flush,
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
                    # Retry the same audio on CPU if a game or sleep/wake invalidates VRAM; retry CUDA after cooldown.
                    worker.engine.close()
                    worker.force_cpu = True
                    worker.cpu_fallback_at = time.monotonic()
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
