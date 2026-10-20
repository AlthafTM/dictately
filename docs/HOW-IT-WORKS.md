# How Dictately works

Dictately is a **local live dictation tool**. It listens while you enable recording and inserts recognized phrases into the text field you selected. It is an independent application built around existing open-source speech models; we did not train Whisper or create a new speech foundation model.

## From speech to typed text

1. **Start:** F12, or your chosen shortcut, starts a recording session. The native Rust controller binds that session to the original editable field and opens the microphone asynchronously
2. **Capture:** Windows supplies 16 kHz mono audio. Short PCM buffers stay in memory and travel through a local pipe to a separate Python speech worker
3. **Recognize:** [faster-whisper](https://github.com/SYSTRAN/faster-whisper) runs OpenAI Whisper through [CTranslate2](https://github.com/OpenNMT/CTranslate2). Compatible NVIDIA GPUs use INT8/FP16; CPU fallback uses INT8
4. **Confirm phrases:** overlapping decodes are compared before words are committed. A changing tail is held back; pauses and stopping flush the remaining words. This is live phrase output, with a delay that depends on the device, model, and speech, rather than guaranteed immediate word-by-word streaming
5. **Clean lightly:** optional rules remove a small set of hesitation sounds and repeated pronouns. Sentence-ending periods are removed. Normal dictation does not run a generative rewriting model, and cannot reliably infer what you meant when the audio was recognized incorrectly
6. **Insert:** a dedicated accessibility worker checks the original destination before injecting text. Changing focus pauses insertion. The app does not press Enter or send messages for you
7. **Correct:** conservative similarity rules can replace a closely matching recent phrase in the same field, or apply supported spoken corrections. These are explicit heuristics with suffix checks, not an AI agent that understands arbitrary editing requests

## AI actually used and available

| Component | Current role | User choice |
| --- | --- | --- |
| [OpenAI Whisper large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo) | Default speech recognition model, using a pinned CTranslate2 conversion | Installed by default |
| [Whisper Small multilingual](https://huggingface.co/Systran/faster-whisper-small) | Smaller compatible speech model | Select `small` for a lower-resource alternative; accuracy can decrease |
| [Whisper Large-v3](https://huggingface.co/Systran/faster-whisper-large-v3) | Compatible full-sized speech model | Select `large-v3` to compare accuracy at greater memory/latency cost |
| faster-whisper / CTranslate2 | Local inference software, not additional trained AI models | Shared by all three choices |
| Silero VAD through faster-whisper | Filters non-speech portions before transcription | Part of the recognition pipeline |

See [Local AI models](AI-MODELS.md) for step-by-step installation, switching, and rollback. Alternative engines require a new adapter and validation; copying arbitrary AI weights into the model folder is not supported. Legacy rewriting experiments in `engine.py` are not started or imported by the live dictation worker.

## Languages and picker

The product currently supports **English** and **Indonesian**. Additional language support may come later. Fixed modes pass an explicit language to Whisper. **Both** filters the detector to those two candidates, including per-segment detection; it does not translate, and rapid switching within one sentence can still cause mistakes.

F10 opens the Windows voice-and-mouse language picker. Saying a language uses short English and Indonesian recognition passes and a strict command parser. Picker commands are never inserted as dictation or saved to history. After the speech model has unloaded, mouse selection and silence do not load it again; a voice command can have a cold-start delay. F12/F10 are configurable in setup and settings.

## Privacy, downloads, and storage

- Speech inference runs on the computer. No account, API key, hosted transcription service, or per-minute subscription is required
- Setup/model selection downloads dependencies and weights. Turbo has pinned revisions and SHA-256 verification; optional models record their resolved download revision
- Normal dictation does not save audio files. PCM is buffered in memory; Windows may page process memory according to system settings
- Original and cleaned transcripts are saved locally, up to 50 history entries. Clear History removes them from the app's history file; it is not a secure disk wipe
- Windows settings/history/status/logs live in `%LOCALAPPDATA%\Dictately`. Existing settings and history from Transcribr are copied when missing; originals remain in the old folder. Models remain beside the installation and are reused during an installer upgrade
- The experimental desktop frontend uses `~/Library/Application Support/Dictately` on macOS or `$XDG_DATA_HOME/dictately` on Linux, defaulting to `~/.local/share/dictately`
- Text inserted into another app becomes subject to that app's own storage and network behavior. Review sensitive text before submitting it

## Limits and tradeoffs

Recognition can make mistakes, especially for names, noise, accents, and mixed speech. Small, Turbo, and Large-v3 have different resource costs; larger does not guarantee better results for your voice. CPU fallback can lag behind normal speech. The worker unloads after about 90 seconds of inactivity to release model resources, so later use may need to warm up again.

Windows uses a native controller and installer. macOS and Linux X11 currently use an experimental Python/Qt frontend with separate controls and platform limitations. This is not the same level of tested integration as Windows. See [platform setup](MACOS-LINUX.md), [comparison](WISPR-FLOW-COMPARISON.md), and [third-party notices](../THIRD_PARTY.md).
