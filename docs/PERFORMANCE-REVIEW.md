# Performance review: Dictately 0.10

Reviewed on 20 October 2026. This review covers shortcut handling, picker and waveform rendering, audio capture, inference, correction matching, settings/history, model setup, and the desktop frontends. Measurements use the development laptop, not a universal performance guarantee.

## Language picker findings

The Windows UI was already Rust. The delay came from rebuilding its geometry, bitmaps, text masks, and option shapes when switching between dictation and the picker. Opening/closing animation also recalculated a distance field and lighting across most of the panel, then repeated horizontal sampling calculations for every row of the moving content.

The updated Rust renderer prewarms both layouts, retains their graphics resources, and exchanges the cached surfaces when switching modes. DPI changes rebuild the affected layout. During transitions it copies the unchanged interior and recalculates only the contour band. Lighting uses a lookup table that preserves the separate inside/outside rim profiles. Content sampling reuses horizontal coordinates and skips transparent samples. Two-times supersampling, premultiplied alpha, the sliding outline, and the opening/closing motion remain.

| Local measurement | 0.9 baseline | Updated renderer |
| --- | ---: | ---: |
| Median shortcut release to window-visible flag | 102.5 ms | 4.5 ms |
| First opening in the measured process | 73.3 ms | 3.1 ms |
| p95 transition frame interval | 24.1 ms | 17.1 ms |
| p95 frame rendering/submission work | 24.0 ms | 8.2 ms |
| Maximum measured frame rendering/submission work | 36.7 ms | 15.6 ms |
| Native private memory while idle, without speech worker | 5.9 MiB | 28.0 MiB |

The measured median activation latency decreased by about **96%**. This measures Win32's window-visible flag; the first nontransparent pixels follow the animation frame clock. The memory increase of about **22 MiB** is the fixed graphics cache: this is a deliberate speed/memory tradeoff, with one-time prewarming at startup. It is separate from speech-model memory. Graphics-object counts remained bounded across repeated switches. These are submitted-frame and visibility measurements, not measured monitor presentation times or a promise that every frame meets its deadline under load.

Method: the same laptop and display scaling, a preserved 0.9 executable, five picker openings per build, actual configurable-default language-shortcut events, and a waveform opening/closing between picker cycles. Synthetic silence prevents microphone capture, model loading, and text insertion. Timing uses a monotonic clock; native frame metrics are opt-in. Initial exploratory runs measured an 88 ms opening median; the table uses the repeat run with memory/resource checks. Background scheduling causes variation.

## Speech and language commands

Language commands previously encoded the same audio twice, once for the English candidate and once for Indonesian. The worker now reuses an encoder result only when its feature array is exactly equal within the same serial command request. It retains at most two inputs, releases them afterward, and restores the original method even on failure. Different audio windows cannot share an encoding. Decoder hypotheses, beam size, language restrictions, and conflict handling remain independent.

| Warm CUDA command benchmark | Separate encoding | Shared encoding |
| --- | ---: | ---: |
| English | 0.725 s | 0.396 s |
| Indonesia | 0.725 s | 0.402 s |
| Both | 0.764 s | 0.431 s |

These synthetic-command measurements show about **44–45% less decoding time** for these fixtures. Each value is the median of three measured repetitions after a warm-up, using the same Turbo model and beam size of three. They exclude microphone opening, pause detection, cold model loading, and UI time. All three command results matched. They do not measure general dictation accuracy or imply that all transcription is 45% faster.

A beam-size-one experiment gave a much smaller improvement, so the existing command decoding settings were retained. Normal dictation still follows the user's accuracy setting. Silent/too-short recognition requests can now return before loading model weights. Standard dictation continues using the existing local CTranslate2 backend.

The real-time Windows controller, rendering, capture, hotkeys, destination tracking, and correction matching stay in Rust. Python orchestrates the local model and the on-demand settings window; the expensive neural work already runs in compiled CTranslate2/CUDA code. Correction matching now borrows token strings instead of cloning candidate suffixes repeatedly.

## Other improvements

- **Settings/history:** the on-demand settings window checks the history file's version metadata and parses JSON only when it changes
- **macOS/Linux idle work:** queued Qt signals wake shortcut/control handling immediately; idle controller polling drops from 25 times per second to once per second, returning to audio cadence during capture/finalization. Waveform motion uses elapsed time instead of assuming a fixed frame rate
- **Model setup:** range-download workers reuse their own HTTP sessions. Responses are streamed with a bounded read after validating range headers. Model fragments and the final file are assembled in bounded blocks; the final SHA-256 is calculated during merging. Only complete, verified output is promoted. Failed checksums discard the owned fragments so a retry can repair them. This removes large join buffers and a separate full-file verification read during new assembly
- **File-decoder compatibility:** PyAV 16.0.1 is pinned in shared requirements because faster-whisper 1.2.1 uses an argument removed by PyAV 19. The existing development environment was not reinstalled for this check

Model-setup tests use tiny mocked downloads and local fragments, including corrupted output, interrupted assembly, retry, and cached restart. No full model download or additional testing software was installed on the development laptop. Reduced temporary allocations and scheduled polling are implementation properties; whole-app battery savings and internet download speed were not measured.

## Verification and remaining limits

The checks exercise native math/rendering, pixel agreement with the reference renderer, reusable graphics resources, settings, devices, language restrictions, exact/scoped encoder reuse, silent requests, PCM framing, correction behavior, model assembly, and Qt capture/finalization. The desktop routing test uses owned editors and verifies that delayed results do not move to another destination. Source checks also exercise synthetic voice language selection, mouse selection, custom shortcuts, live insertion, and corrections.

GitHub Actions checks Windows, macOS Intel, macOS Apple Silicon, and Linux X11. Windows adds a synthetic posted-message picker/resource check; the local benchmark additionally uses real shortcut events. Mac/Linux checks include real Small-model CPU recognition and command selection; Linux additionally runs complete synthetic dictation into a real X11 test field. The Windows installer is built in the cloud with frozen-worker startup verification.

Cold inference startup, larger-model latency, background noise, and third-party editor compatibility remain device-dependent. macOS/Linux still need real-device microphone/permission/app validation. The source frontends are experimental, and Wayland remains unsupported. There is no claim that this review exhaustively certifies every device or application.

## Reproduce

With the normal controller stopped, an existing source environment, and the generated native executable:

```powershell
.\scripts\windows\build-native.ps1 test --release --locked
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -q
.\.venv\Scripts\python.exe tests/check_picker_latency.py
.\.venv\Scripts\python.exe tests/check_animation.py
.\.venv\Scripts\python.exe tests/check_animation.py --waveform
```

`check_picker_latency.py --baseline --exe <old-executable>` records a comparison. `--messages` uses posted control messages for cloud runners; it is not a measurement of the physical keyboard path. Results stay under ignored `qa/`. Recognition fixtures are synthesized, and their benchmark does not stand in for a varied real-speaker accuracy evaluation.

The approach follows [Microsoft's guidance on keeping UI work responsive](https://learn.microsoft.com/en-us/windows/apps/develop/performance/keep-ui-thread-responsive), [CTranslate2 performance guidance](https://opennmt.net/CTranslate2/performance.html), and [Requests session/streaming guidance](https://requests.readthedocs.io/en/latest/user/advanced/). These references guide the techniques; the numerical results above come from the local measurements.
