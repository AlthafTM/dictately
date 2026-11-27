"""Pure speech-gate, anti-hallucination, dictionary, snippet, and Indonesian text processing."""
from __future__ import annotations

from datetime import datetime
import re
import unicodedata

# Whisper-family decoders retain at most 223 prompt tokens (~550 chars of comma-separated names).
WHISPER_PROMPT_BUDGET_CHARS = 550

# Local speech gate thresholds: suppress room hiss while keeping soft speech and synthetic fixtures.
SILENCE_RMS_THRESHOLD = 0.002
SPEECH_WINDOW_RMS_THRESHOLD = 0.003
SPEECH_WINDOW_PEAK_THRESHOLD = 0.02
STRONG_SPEECH_RMS_THRESHOLD = 0.006

# Whisper no-speech signature (matches openai/whisper defaults: no_speech_prob > 0.6 & avg_logprob <= -1.0).
NO_SPEECH_PROB_THRESHOLD = 0.6
NO_SPEECH_LOGPROB_THRESHOLD = -1.0

# Known Whisper training-data subtitle/outro hallucinations (Indonesian & English).
_HALLUCINATION_PHRASES = frozenset({
    "terima kasih telah menonton",
    "terima kasih sudah menonton",
    "terima kasih telah menonton video ini",
    "terima kasih sudah menonton video ini",
    "jangan lupa subscribe",
    "jangan lupa like komen dan subscribe",
    "jangan lupa like comment dan subscribe",
    "sampai jumpa di video selanjutnya",
    "sampai jumpa di video berikutnya",
    "selamat menonton",
    "subtitle oleh",
    "subtitles by",
    "thank you for watching",
    "thanks for watching",
    "please subscribe",
    "thanks for listening",
    "amara org",
})

# Common Indonesian grammatical reduplication (kata ulang) that Whisper often splits with spaces.
# Explicitly protected from stutter collapsing and normalized to standard EYD hyphenation.
INDONESIAN_REDUPLICATIONS = frozenset({
    "anak", "apa", "bapak", "barang", "benar", "besar", "betul", "buru",
    "cepat", "cuma", "dekat", "diam", "hari", "hati", "ibu", "jalan",
    "kadang", "kecil", "kira", "lagi", "lama", "macam", "malu", "masing",
    "mudah", "orang", "pelan", "pura", "ragu", "rata", "sama", "siap",
    "sia", "teman", "tiba", "ulang",
})
_REDUPLICATION_RE = re.compile(
    r"\b(" + "|".join(sorted(INDONESIAN_REDUPLICATIONS)) + r")[\s]+\1\b",
    re.IGNORECASE,
)

# Conservative colloquial-to-baku Indonesian mapping (opt-in only).
_FORMAL_INDONESIAN_MAP = {
    "gak": "tidak",
    "nggak": "tidak",
    "ngga": "tidak",
    "ga": "tidak",
    "tdk": "tidak",
    "udah": "sudah",
    "sdh": "sudah",
    "blm": "belum",
    "gimana": "bagaimana",
    "kenapa": "mengapa",
    "emang": "memang",
    "cuma": "hanya",
    "cuman": "hanya",
    "ntar": "nanti",
    "entar": "nanti",
    "soalnya": "karena",
    "krn": "karena",
    "makasih": "terima kasih",
    "bgt": "sangat",
    "yg": "yang",
    "dgn": "dengan",
    "utk": "untuk",
    "tp": "tetapi",
    "jg": "juga",
    "dpt": "dapat",
}
_FORMAL_INDONESIAN_RE = re.compile(
    r"(?<!\w)(" + "|".join(sorted(_FORMAL_INDONESIAN_MAP, key=len, reverse=True)) + r")(?!\w)",
    re.IGNORECASE,
)

_ID_MONTHS = (
    "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember",
)
_EN_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

_STUTTER_PRONOUNS_RE = re.compile(
    r"\b(i|you|we|they|saya|aku|kami|kita|kamu|anda|dia|mereka|gue|gua|lu|lo)[,\s]+\1\b",
    re.IGNORECASE,
)
_FILLERS_EN_RE = re.compile(r",?\s*\b(?:um+|uh+|erm|hmm+|eh)\b[,\s]*", re.IGNORECASE)
_FILLERS_ID_RE = re.compile(r",?\s*\b(?:um+|uh+|erm|hmm+|ehm+|emm+|eee+|eh+|anu)\b[,\s]*", re.IGNORECASE)
_PROMPT_SPLIT_RE = re.compile(r"[,;\r\n、，]+")
_SENTENCE_BREAK_RE = re.compile(r"[.!?]\s+")
_NON_TERMINAL_PERIOD_RE = re.compile(
    r"(?:(?:^|[\s(])(?:etc|vs|mrs?|ms|dr|st|jr|prof|no|pt|cv|jl|hal|dkk|dsb|dst|yaitu|\d{1,2})|[A-Za-z]\.[A-Za-z])$|\.$",
    re.IGNORECASE,
)


def _norm_key(text: str) -> str:
    text = unicodedata.normalize("NFC", text).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def has_speech_energy(audio, sample_rate: int = 16000) -> bool:
    """Return True when float32 [-1, 1] audio contains windowed speech energy above room noise."""
    import numpy as np

    if len(audio) < 3200:
        return False
    window = max(1, sample_rate // 50)  # 20 ms frames (320 samples at 16 kHz)
    trimmed = len(audio) - (len(audio) % window)
    if trimmed == 0:
        return False
    frames = np.asarray(audio[:trimmed], dtype=np.float32).reshape(-1, window)
    frame_rms = np.sqrt(np.mean(frames * frames, axis=1))
    frame_peak = np.max(np.abs(frames), axis=1)
    peak_rms = float(np.max(frame_rms))
    if peak_rms < SILENCE_RMS_THRESHOLD:
        return False
    speech_windows = int(np.sum((frame_rms >= SPEECH_WINDOW_RMS_THRESHOLD) & (frame_peak >= SPEECH_WINDOW_PEAK_THRESHOLD)))
    return speech_windows >= 1 or peak_rms >= STRONG_SPEECH_RMS_THRESHOLD


def is_hallucination_segment(segment) -> bool:
    """Detect Whisper silence/outro hallucination segments while keeping quiet real speech."""
    text = getattr(segment, "text", "") or ""
    normalized = _norm_key(text)
    if not normalized:
        return True
    no_speech = getattr(segment, "no_speech_prob", None)
    avg_logprob = getattr(segment, "avg_logprob", None)
    if (
        isinstance(no_speech, (int, float))
        and isinstance(avg_logprob, (int, float))
        and no_speech > NO_SPEECH_PROB_THRESHOLD
        and avg_logprob <= NO_SPEECH_LOGPROB_THRESHOLD
    ):
        return True
    if normalized in _HALLUCINATION_PHRASES:
        # Known YouTube/subtitle boilerplate: drop when confidence is weak or metadata is absent on isolated outro.
        if no_speech is None or avg_logprob is None:
            return True
        if no_speech >= 0.35 or avg_logprob <= -0.65:
            return True
    return False


def filter_segments(segments) -> list:
    """Remove silence-hallucinated segments while preserving real speech segments."""
    return [seg for seg in segments if not is_hallucination_segment(seg)]


def parse_vocabulary_terms(vocabulary: str, limit: int = 60) -> list[str]:
    """Parse user vocabulary preserving acronyms (KTP, API), symbols (Node.js, Wi-Fi), and names."""
    if not vocabulary:
        return []
    terms = []
    seen = set()
    for raw in _PROMPT_SPLIT_RE.split(str(vocabulary)):
        term = re.sub(r"\s+", " ", raw).strip()
        if not (2 <= len(term) <= 48) or not any(ch.isalnum() for ch in term):
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
        if len(terms) >= limit:
            break
    return terms


def cap_vocabulary_prompt(vocabulary: str, snippets=None, max_chars: int = WHISPER_PROMPT_BUDGET_CHARS) -> str:
    """Build a bounded Whisper hotword/prompt string prioritizing personal vocabulary then snippet triggers."""
    terms = parse_vocabulary_terms(vocabulary)
    seen = {t.casefold() for t in terms}
    if snippets:
        for item in snippets:
            trigger = ""
            if isinstance(item, dict):
                trigger = str(item.get("trigger") or "").strip()
            elif isinstance(item, (list, tuple)) and len(item) >= 1:
                trigger = str(item[0] or "").strip()
            if 2 <= len(trigger) <= 48 and trigger.casefold() not in seen:
                seen.add(trigger.casefold())
                terms.append(trigger)
    kept = []
    length = 0
    for term in terms:
        needed = len(term) + (2 if kept else 0)
        if length + needed > max_chars:
            break
        kept.append(term)
        length += needed
    return ", ".join(kept)


def filter_dictionary_echo(text: str, sent_prompt: str, dictionary_terms=None) -> str:
    """Discard full-prompt dictionary echoes and strip trailing dictionary recitals."""
    if not text or not sent_prompt:
        return text
    prompt_items = [_norm_key(t) for t in _PROMPT_SPLIT_RE.split(sent_prompt) if _norm_key(t)]
    if not prompt_items:
        return text
    norm_text = _norm_key(text)
    if not norm_text:
        return ""
    norm_prompt = " ".join(prompt_items)
    if norm_text == norm_prompt and len(prompt_items) >= 2:
        return ""

    # Whole-utterance recital of 2+ consecutive/ordered prompt terms covering >=90% of utterance & >=70% of prompt.
    text_words = norm_text.split()
    prompt_words = norm_prompt.split()
    if len(prompt_items) >= 2 and len(text_words) >= 2:
        transcript = f" {norm_text} "
        unique_text = set(text_words)
        matched_words = set()
        offset = 0
        matched_terms = 0
        for item in prompt_items:
            parts = item.split()
            if parts[0] not in unique_text:
                continue
            pos = transcript.find(f" {item} ", offset)
            if pos == -1:
                continue
            offset = pos + 1 + len(item)
            matched_terms += 1
            matched_words.update(parts)
        if (
            matched_terms >= 2
            and len(matched_words) / len(unique_text) >= 0.9
            and len(matched_words) / max(1, len(set(prompt_words))) >= 0.7
        ):
            return ""

    # Trailing dictionary recital after a real sentence ("... besok pagi. Senko, Jakarta").
    dict_set = {
        _norm_key(t)
        for t in (dictionary_terms if dictionary_terms is not None else prompt_items)
        if _norm_key(t)
    }
    if len(prompt_items) < 2 or len(dict_set) < 2:
        return text
    term_indexes = {}
    for idx, item in enumerate(prompt_items):
        term_indexes.setdefault(item, idx)

    body = text.rstrip()
    tail_start = -1
    for match in _SENTENCE_BREAK_RE.finditer(body):
        end = match.end()
        if end >= len(body):
            continue
        if match.group(0)[0] == "." and _NON_TERMINAL_PERIOD_RE.search(body[:match.start()]):
            continue
        tail_start = end
    if tail_start <= 0:
        return text

    tail_items = [_norm_key(t) for t in _PROMPT_SPLIT_RE.split(body[tail_start:]) if _norm_key(t)]
    if len(tail_items) < 2 or tail_items[0] not in dict_set:
        return text
    if sum(1 for item in tail_items if item in dict_set) < 2:
        return text
    indexes = [term_indexes.get(item) for item in tail_items]
    if any(i is None for i in indexes):
        return text
    if all(indexes[i] == indexes[i - 1] + 1 for i in range(1, len(indexes))):
        opens_prompt = indexes[0] == 0
        closes_prompt = len(indexes) >= 5 and indexes[-1] == len(prompt_items) - 1
        if opens_prompt or closes_prompt:
            return body[:tail_start].rstrip()
    return text


def apply_replacements(text: str, replacements) -> str:
    """Apply explicit user word/phrase replacements on Unicode word boundaries (longest-first)."""
    if not text or not replacements:
        return text
    pairs = {}
    items = replacements.items() if isinstance(replacements, dict) else replacements
    for entry in items:
        if isinstance(entry, dict):
            src = str(entry.get("source") or "").strip()
            dst = str(entry.get("target") or "").strip()
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            src = str(entry[0] or "").strip()
            dst = str(entry[1] or "").strip()
        else:
            continue
        if src and dst:
            pairs[src.casefold()] = dst
    if not pairs:
        return text
    ordered = sorted(pairs.keys(), key=len, reverse=True)
    pattern = re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(k) for k in ordered) + r")(?!\w)",
        re.IGNORECASE,
    )
    return pattern.sub(lambda m: pairs[m.group(0).casefold()], text)


def _snippet_variables(language: str, now: datetime) -> dict[str, str]:
    id_date = f"{now.day} {_ID_MONTHS[now.month - 1]} {now.year}"
    en_date = f"{_EN_MONTHS[now.month - 1]} {now.day}, {now.year}"
    id_time = now.strftime("%H.%M")
    en_time = now.strftime("%I:%M %p").lstrip("0")
    if language == "id":
        return {
            "date": id_date,
            "time": id_time,
            "datetime": f"{id_date}, {id_time}",
            "tanggal": id_date,
            "waktu": id_time,
            "tanggal_waktu": f"{id_date}, {id_time}",
        }
    return {
        "date": en_date,
        "time": en_time,
        "datetime": f"{en_date} {en_time}",
        "tanggal": id_date,
        "waktu": id_time,
        "tanggal_waktu": f"{id_date}, {id_time}",
    }


def resolve_snippet_variables(replacement: str, language: str = "en", now: datetime | None = None) -> str:
    if "{{" not in replacement:
        return replacement
    values = _snippet_variables(language, now or datetime.now())
    return re.sub(
        r"\{\{([A-Za-z][A-Za-z0-9_]*)\}\}",
        lambda m: values.get(m.group(1).lower(), m.group(0)),
        replacement,
    )


def expand_snippets(text: str, snippets, language: str = "en", now: datetime | None = None) -> str:
    """Replace spoken snippet triggers with saved expansions in one longest-first pass."""
    if not text or not snippets:
        return text
    table = {}
    items = snippets.items() if isinstance(snippets, dict) else snippets
    for entry in items:
        if isinstance(entry, dict):
            trigger = str(entry.get("trigger") or "").strip()
            replacement = str(entry.get("replacement") or "")
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            trigger = str(entry[0] or "").strip()
            replacement = str(entry[1] or "")
        else:
            continue
        if trigger and replacement:
            table[unicodedata.normalize("NFC", trigger).casefold()] = replacement
    if not table:
        return text
    ordered = sorted(table.keys(), key=len, reverse=True)
    pattern = re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(k) for k in ordered) + r")(?!\w)",
        re.IGNORECASE,
    )
    stamp = now or datetime.now()
    normalized_text = unicodedata.normalize("NFC", text)
    return pattern.sub(
        lambda m: resolve_snippet_variables(table[m.group(0).casefold()], language=language, now=stamp),
        normalized_text,
    )


def normalize_indonesian_reduplication(text: str) -> str:
    """Join common space-separated Indonesian kata ulang (e.g. 'hati hati' -> 'hati-hati')."""
    if not text:
        return text

    def _join(match):
        first = match.group(1)
        second = match.group(0).split()[-1]
        return f"{first}-{second.lower()}"

    return _REDUPLICATION_RE.sub(_join, text)


def _match_case(source: str, target: str) -> str:
    if source.isupper() and len(source) > 1:
        return target.upper()
    if source and source[0].isupper():
        return target[0].upper() + target[1:]
    return target


def normalize_formal_indonesian(text: str) -> str:
    """Conservatively convert common colloquial Indonesian shorthand/words into baku forms."""
    if not text:
        return text
    return _FORMAL_INDONESIAN_RE.sub(
        lambda m: _match_case(m.group(1), _FORMAL_INDONESIAN_MAP[m.group(1).lower()]),
        text,
    )


def capitalize_lowercase_utterance(text: str, is_phrase_start: bool = True) -> str:
    """Capitalize the opening letter when a lowercase-only fine-tuned model emits all-lowercase text."""
    if not text or not is_phrase_start:
        return text
    if any(ch.isupper() for ch in text):
        return text
    for idx, ch in enumerate(text):
        if ch.isalpha():
            return text[:idx] + ch.upper() + text[idx + 1:]
    return text


def clean_dictation(
    text: str,
    language: str = "en",
    cleanup: bool = True,
    formal_indonesian: bool = False,
    capitalize_lowercase: bool = False,
    is_phrase_start: bool = True,
) -> str:
    """Conservative cleanup tuned for English and Indonesian without altering meaning."""
    if not text:
        return ""
    result = text
    if cleanup:
        filler_re = _FILLERS_ID_RE if language == "id" else _FILLERS_EN_RE
        result = filler_re.sub(" ", result)
        for _ in range(3):
            result = _STUTTER_PRONOUNS_RE.sub(r"\1", result)
        result = re.sub(r"\s+", " ", result).strip(" ,")
        if language == "id":
            result = normalize_indonesian_reduplication(result)
    if language == "id" and formal_indonesian:
        result = normalize_formal_indonesian(result)
    if capitalize_lowercase:
        result = capitalize_lowercase_utterance(result, is_phrase_start=is_phrase_start)
    return re.sub(r"\s+", " ", result).strip()
