# Transcribr compared with Wispr Flow

Assessment date: **20 October 2026**. Scope: the current Transcribr source and public Wispr Flow documentation, with Windows as the main comparison platform. This is a feature and engineering assessment, not a reverse-engineering report or a controlled accuracy competition. No Wispr account, private conversation, or customer audio was accessed.

## Main finding

Transcribr already provides the core local dictation loop: F12, microphone capture, an animated waveform, fixed **English** / **Indonesian**, live phrase insertion into a protected destination, conservative spoken corrections, vocabulary hints, history, and local model choices. Its main gaps are contextual editing, automatic personalization, product packaging, and measured recognition quality across real voices and applications.

We cannot honestly give a percentage accuracy gap against Flow: neither product has been tested on the same recordings in this project. Faster transport improves efficiency; it does not make the speech model recognize words more accurately.

## At a glance: our strengths and weaknesses

| Classification | What this means for a Transcribr user |
| --- | --- |
| **Our strength: local and offline** | Recognition stays on your device after setup, including when the internet is unavailable; Flow processes dictation audio in the cloud. [Flow security overview](https://docs.wisprflow.ai/articles/3467817258-security-and-compliance-faq) |
| **Our strength: no transcription subscription** | No account, API key, or usage-based transcription bill is required by Transcribr; you supply the hardware and electricity |
| **Our strength: local model control** | Choose compatible Small, Turbo, or Large-v3 weights, inspect the code, and keep models/history under your own control |
| **Our weakness: less understanding of context** | Names, writing style, and surrounding text do not receive Flow's contextual assistance; vocabulary hints are largely manual. [Context Awareness](https://docs.wisprflow.ai/articles/4678293671-feature-context-awareness) |
| **Our weakness: less capable editing** | Basic cleanup and recent-suffix correction do not match general voice editing, richer formatting, snippets, or automatic dictionary learning; details and sources appear below |
| **Our weakness: narrower support and maintenance** | Only **English** / **Indonesian** and Windows x64; the setup wizard bundles its runtime, but mobile apps, code signing, automatic updates, and a GUI model selector remain missing |
| **Our tradeoff: your device does the work** | Offline operation moves inference cost onto your CPU/GPU; model loading, memory pressure, battery use, and phrase delay still matter |
| **Not established: accuracy or speed superiority** | No matched real-speech benchmark against Flow exists here. Feature differences and synthetic tests do not prove which app recognizes your voice better |

Choose Transcribr when local processing and control matter most. Expect Flow to offer more of the documented editing/personalization ecosystem. Treat recognition quality on your own speech as something to measure, rather than assume.

## Our weaknesses compared with Wispr Flow

| Area | Transcribr today | What Flow offers / what is missing |
| --- | --- | --- |
| Recognition quality | One local Whisper model at a time; optional Small, Turbo, or Large-v3 | Wispr describes a recognition-model ensemble. This differs from our single-engine pipeline, but its description does not establish a measured advantage on your voice. [Wispr language research](https://wisprflow.ai/research/supporting-languages) |
| App and text context | Editor context is used for destination verification and limited correction, not general recognition prompting | Flow uses app context and nearby text to adapt names, tone, and formatting. Windows support is partial; some capabilities remain Mac-only. [Context Awareness](https://docs.wisprflow.ai/articles/4678293671-feature-context-awareness) |
| Formatting and cleanup | Small rule-based filler/repeated-pronoun cleanup; removes terminal periods; no full generative editor in the live path | Flow documents Smart Formatting, Auto Cleanup, and Backtrack for structured output and self-corrections. Our rules do not provide equivalent semantic rewriting. [Formatting and Backtrack](https://docs.wisprflow.ai/articles/5373093536-how-do-i-use-smart-formatting-and-backtrack) |
| General voice editing | Similar recent sentence replacement or explicit last-word correction; same field, verified suffix, 90-second window | Desktop Command Mode can edit text, draft replies, and search using spoken instructions. We have no equivalent arbitrary instruction mode. [Command Mode](https://docs.wisprflow.ai/articles/4816967992-how-to-use-command-mode) |
| Dictionary learning | Users manually enter vocabulary hints; the live path does not learn from manual edits | Flow can watch post-insertion corrections when Auto-add to Dictionary is enabled. We lack that learning loop and a proper replacement dictionary. [Data controls](https://wisprflow.ai/data-controls) |
| Reusable snippets | No phrase-to-template expansion | Flow supports spoken triggers that expand into stored snippets. This is a useful feature we could implement locally without a larger speech model. [Snippets](https://docs.wisprflow.ai/articles/5784437944-create-and-use-snippets) |
| Language breadth | **English**, **Indonesian**, or **Both**; voice/mouse picker; Both restricts segment detection to these two languages | Flow supports 100+ languages and regional variants, and can select a language per dictation. It does not claim per-word detection in its current guide. Additional Transcribr languages are possible future work, not currently supported modes. [Languages](https://docs.wisprflow.ai/articles/3191899797) |
| Hotkeys and input choices | Configurable keyboard shortcuts (default F12 dictation, F11 voice/mouse language picker), Esc cancellation; Windows default microphone | Flow documents configurable keyboard/mouse shortcuts and push-to-talk/hands-free controls. We still lack mouse-button bindings, push-to-talk, and an in-app microphone picker/test. [Shortcuts](https://docs.wisprflow.ai/articles/2612050838-supported-unsupported-keyboard-hotkey-shortcuts) |
| Platforms | Windows x64 only | Flow is available on Mac, Windows, iOS, and Android, with platform-dependent features. We have no mobile keyboard, Mac app, or cross-device experience. [Product overview](https://docs.wisprflow.ai/articles/2772472373-what-is-flow) |
| Team use | Local settings and manual vocabulary, no accounts or team service | Flow offers shared dictionary/snippet libraries and organization controls. Transcribr has no team synchronization or admin console. [Shared entries](https://docs.wisprflow.ai/articles/9639977157-view-shared-dictionary-words-and-snippets-in-the-admin-portal) |
| Installation and maintenance | A per-user setup wizard bundles the runtime and handles model download, shortcuts, startup, and uninstall; source builds still need Python and Rust | Code signing, clean-machine installation coverage, automatic update/rollback, and a GUI model selector remain unfinished |
| Reliability evidence | Unit tests, protocol tests, synthetic speech and disposable-editor tests; known browser/custom-editor variability | We lack a broad, versioned compatibility matrix and real-speech accuracy/latency suite. Flow also documents app, network, and language limitations; neither should be described as infallible. [Flow limitations](https://docs.wisprflow.ai/articles/4048537120-what-to-expect-from-flow-accuracy-and-known-limitations) |

## Our strengths and their practical limits

**Local/offline recognition:** after installation, audio is processed on your own device. Normal dictation does not save audio files. Flow's security documentation explicitly says it processes dictation audio in the cloud; privacy and retention controls do not turn cloud inference into offline inference. [Flow security overview](https://docs.wisprflow.ai/articles/3467817258-security-and-compliance-faq)

**No transcription subscription or API key:** Transcribr's runtime has no cloud per-minute bill. Local hardware, storage, electricity, and installation effort remain costs. Model weights and dependencies have their own licenses; see [third-party notices](../THIRD_PARTY.md).

**Explicit local model choice:** users can change compatible Whisper models and keep their data on the device. The default is the tested Turbo setup; selecting a bigger model is an experiment, not a guarantee of better dictation. See [AI model setup](AI-MODELS.md).

Local storage still deserves work: history currently retains up to 50 transcripts as local JSON, without an app-level encryption layer or a “never save history” option. Flow documents more granular retention choices; adding a local history opt-out would be worthwhile. [Flow history controls](https://docs.wisprflow.ai/articles/9609615338-private-cloud-sync-and-data-sharing-preferences-in-wispr-flow)

## Optimization implemented in this update

Previously the native UI constructed a `serde_json::Value` for every PCM sample, then the worker serialized those integers and Python parsed them back. An 18-second buffer contains **288,000 samples**. That work happened repeatedly as the current phrase grew.

The controller now copies a contiguous signed 16-bit sample buffer to its worker thread. That thread sends a small JSON header followed by exact little-endian PCM bytes. Python reads directly into a byte buffer and creates a NumPy view before normalizing once to float32. The silence-energy calculation also avoids allocating an additional squared-audio array. There is no audio compression, resampling change, or model change.

Microphone collection now appends directly into the controller's audio buffer and clears the capture queue while retaining its allocation. Previously every queue take discarded that reusable allocation, and the following microphone callback had to allocate again. The synthetic-input path also appends slices directly instead of creating a temporary vector. Allocation churn is reduced by construction; whole-app RAM/CPU savings have not been independently measured.

### Measured transport results

Development laptop: Ryzen 7 7735HS, RTX 3050 Laptop 6 GB, approximately 16 GB installed system RAM. Measurements below are a **Python receive/parsing microbenchmark**, not model inference, microphone latency, Rust dispatch timing, or whole-app CPU/memory consumption.

| Synthetic audio | Legacy JSON bytes | PCM frame bytes | Byte reduction | Legacy parsing median | PCM parsing median |
| --- | ---: | ---: | ---: | ---: | ---: |
| 3 seconds | 295,857 | 96,074 | 67.5% | 3.267 ms | 0.007 ms |
| 18 seconds | 1,774,678 | 576,075 | 67.5% | 23.211 ms | 0.040 ms |

Method: fixed-seed full-range signed samples at 16 kHz mono, compact JSON baseline, 3 warmups and 25 measured runs per operation, median elapsed time. The test asserts exact sample equality. Real microphone amplitude affects JSON size, so the percentage is not universal. Pipe waits, inference, and editor insertion are excluded. These results do **not** mean that dictation becomes hundreds of times faster; inference remains the dominant cost.

Reproduce from the app root:

```powershell
.\.venv\Scripts\python.exe tests/benchmark_transport.py
```

The protocol tests cover partial pipe reads, signed sample extremes, newline bytes within audio, consecutive frames, malformed counts, and truncated payloads. Native tests check the matching wire format. The frame reader bounds audio to 150 seconds; the regular UI still limits recording sessions to a maximum of 120 seconds.

Validation in this update: **17 Python tests** and **6 native unit tests** passed; the desktop-only native unit test remains ignored in that default run. Two 18-second synthetic desktop dictations produced text while recording and left the second field untouched. Their reference-word recall was 100% and 98%, respectively; recall is not word error rate and these are not real-speaker accuracy results. A final short synthetic insertion check passed after the microphone-buffer change, and the real-microphone/overlay start-cancel lifecycle check passed. The quick 2.8-second phrase was inserted after recording ended, illustrating the remaining phrase-buffering delay.

## F12 startup stutter: cause and fix

Windows microphone open/start originally ran on the same thread as the overlay and low-level keyboard hook. On this laptop, it blocked that thread for roughly 0.5–1 second at recording start. This delayed the opening animation and made UI/hotkey handling unresponsive during the driver call.

Microphone open/start/stop/close now runs on a dedicated audio-owner thread. F12 starts the visual cue immediately while the driver initializes. Session-tagged completions and a cancellation token prevent delayed or cancelled starts from attaching to a new session. Final transcription waits for the stop completion's remaining audio instead of discarding it.

| Responsiveness check | Before, first start | After, first start | Before, second start | After, second start |
| --- | ---: | ---: | ---: | ---: |
| Overlay visible after toggle | 1,013.3 ms | 12.8 ms | 493.6 ms | 12.8 ms |
| UI ping timeouts, 25 ms limit | 21 | 0 | 10 | 0 |

Method: isolated controller, real Windows microphone, no text insertion, polling window responsiveness and visibility. The **after** run additionally injected a 600 ms microphone-start delay to verify that slow driver setup no longer freezes the UI. Its largest measured UI ping was 4.0 ms. These are two local runs, not universal latency guarantees or an audio/recognition benchmark.

The microphone itself still needs initialization: it became ready after 1.13–1.55 seconds in that artificially delayed run. A fast visual cue does not mean hardware capture or the speech model is instant. The waveform receives real microphone levels once capture is ready; the idle app does not keep a microphone recording open.

Validation also checked real sample collection, cancellation during a pending open, immediate restart without stale session binding, and normal stop/finalization. The default native suite now has **7 passing tests** and one ignored desktop-only test. The existing overlay lifecycle and short desktop dictation checks passed with this audio-thread change.

## Recommended next work, in priority order

1. **Measure the real failure cases.** Build an opt-in, local evaluation set of English and Indonesian speech with manually checked references. Track word error rate, numbers/names/negation errors, first inserted phrase, finalization delay, peak VRAM, and editor-routing failures. Use identical clips for each model and keep recordings out of Git. Any Flow comparison needs the same clips and a deliberate decision to send those clips to its cloud service
2. **Choose the speech model using evidence.** Compare Turbo and Large-v3 on this dataset before changing the default. More context can improve recognition but delays live commits; expose a clear speed/accuracy choice rather than increasing every buffer blindly
3. **Add a real local dictionary and snippets.** Store explicit replacements and phrase expansions; let users approve learned terms and undo replacements. This helps names and repetitive workflows without loading a second large AI model
4. **Improve local context carefully.** Offer opt-in nearby-text hints for names and terms, with a small bounded context window and password exclusions. Preserve the existing original-field checks. Do not copy full conversations merely to improve spelling
5. **Add optional semantic editing separately.** A local editor could resolve more false starts and format lists, but it adds model memory, latency, and the possibility of changing meaning. Keep literal dictation available, verify numbers/negation, and make undo possible
6. **Finish usability and distribution.** The basic installer now bundles the runtime and handles first-run setup. Add a GUI model selector, configurable hotkeys, microphone selection/test, history opt-out, code signing, and a tested update/rollback path
7. **Expand languages after validation.** Add one explicit language mode at a time with native-speaker test cases; preserve manual language locking. The underlying multilingual model alone is not proof the app's cleanup/corrections support a new language correctly

The highest-value next step is measuring recognition on your voice and fixing vocabulary/context errors. More waveform polish or smaller transport buffers cannot by themselves close the semantic-editing gap.
