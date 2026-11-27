# Local AI models: choose, install, and switch

Dictately runs speech recognition **locally on your computer**. These options do not use a cloud transcription API, require an API key, or charge per minute. Internet access is needed to download dependencies and model files; afterward the worker loads local files only. Electricity, storage, and hardware are your own costs.

## Which model should I choose?

| Option | Use it for | Tradeoff |
| --- | --- | --- |
| `turbo`: Whisper large-v3-turbo | Default everyday English & Indonesian dictation, especially with an NVIDIA GPU | Practical speed/accuracy balance with full casing and punctuation; ~1.54 GiB weight download |
| `turbo-id`: `maleo-ai/whisper-large-v3-turbo-id` (INT8, MIT) | **Best local Indonesian accuracy** (recommended as a dedicated Indonesian model via `--for-indonesian`) | Lower Indonesian WER at half the weight size (~776 MiB), but outputs lowercase without punctuation (Dictately capitalizes phrase starts automatically) |
| `small-id`: `maleo-ai/whisper-small-id` (INT8, Apache-2.0) | Lightweight or CPU-only **Indonesian** dictation | Much lower Indonesian WER than generic `small` (~233 MiB), lowercase without punctuation |
| `small`: Whisper Small multilingual | Lower memory use or trying CPU-only EN/ID dictation | Smaller model (~461 MiB), less accurate than Turbo or `small-id` |
| `large-v3`: full Whisper large-v3 | Testing whether a larger 32-layer decoder reduces mistakes on your voice | More storage (~3.0 GB), memory, and processing; live output may lag more |

All five use the same Windows **English / Indonesian** controls, F10 voice/mouse picker, configurable F12/F10 defaults, local history, corrections, snippets, and destination protection. Because `turbo-id` and `small-id` are fine-tuned specifically for Bahasa Indonesia and omit punctuation, the recommended setup for bilingual users is keeping **`turbo`** as your default/English model and setting **`turbo-id`** (or **`small-id`** on CPU) as your dedicated **Indonesian model**.

### Local Indonesian benchmark (RTX 3050 6 GB)

Measured on **20 native Indonesian utterances** from the public [Google FLEURS `id_id` test split](https://huggingface.co/datasets/google/fleurs) (`faster-whisper` 1.2.1, CUDA `int8_float16`, `beam_size=5`, `vad_filter=True`, lowercase & punctuation-stripped WER/CER):

| Model | `model.bin` size | FLEURS `id_id` WER (20 clips) | CER | Median latency | RTF | Casing / Punct |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| **`turbo-id`** (`maleo-ai/whisper-large-v3-turbo-id`) | **776.3 MiB** | **6.80%** | **2.57%** | 0.625 s | 0.0529 | Lowercase (auto phrase-start cap) |
| `turbo` (`dropbox-dash/faster-whisper-large-v3-turbo`) | 1,542.9 MiB | 7.81% | 2.64% | 0.594 s | 0.0505 | Preserved (`20/20` / `18/20`) |
| **`small-id`** (`maleo-ai/whisper-small-id`) | **233.3 MiB** | **9.32%** | **3.35%** | 0.844 s | 0.0784 | Lowercase (auto phrase-start cap) |
| `small` (`Systran/faster-whisper-small`) | 461.1 MiB | 14.36% | 3.87% | 0.515 s | 0.0469 | Preserved (`18/20` / `17/20`) |

These measurements are a bounded 20-clip public test-set check on one RTX 3050 Laptop GPU and align directionally with `maleo-ai`'s published full-split results (FLEURS 684 utts: 6.4% vs 7.1%; Common Voice 17 `id` 3,629 utts: 7.7% vs 10.1%). Note that non-Whisper fast ONNX families such as NVIDIA Parakeet, Orukeet, Nemotron 3.5 ASR, and Cohere Transcribe 2B do not currently support Indonesian.

Turbo is a pruned version of Large-v3 with 4 decoder layers instead of 32, trading some quality for speed: see the [Turbo model card](https://huggingface.co/openai/whisper-large-v3-turbo). The optional [Small](https://huggingface.co/Systran/faster-whisper-small), [Large-v3](https://huggingface.co/Systran/faster-whisper-large-v3), [Turbo-ID](https://huggingface.co/maleo-ai/whisper-large-v3-turbo-id), and [Small-ID](https://huggingface.co/maleo-ai/whisper-small-id) packages are CTranslate2 conversions compatible with the existing worker.

## Step by step: install a different model

You can switch or download models from **Tray → History & settings → Settings & models → Local speech models**, or from PowerShell:

1. Complete the normal [installation](../README.md#download-and-install) first. Keep the app in its permanent folder
2. Stop recording, then choose **Quit** in the tray menu, or run the following in PowerShell from the app folder

   ```powershell
   & '.\scripts/windows/Stop Dictately.ps1'
   ```

3. Download and select **one** model (or add `--for-indonesian` to route only Indonesian to a fine-tuned model while keeping English on Turbo). **Installer users** use the included worker:

   ```powershell
   # Recommended for Indonesian + English bilingual users (keeps English on Turbo):
   .\runtime\worker\DictatelyWorker.exe --select-model turbo-id --for-indonesian

   # Or lightweight CPU-friendly Indonesian model:
   .\runtime\worker\DictatelyWorker.exe --select-model small-id --for-indonesian

   # Or switch the default model for both languages:
   .\runtime\worker\DictatelyWorker.exe --select-model small
   .\runtime\worker\DictatelyWorker.exe --select-model large-v3

   # Clear a dedicated Indonesian model override:
   .\runtime\worker\DictatelyWorker.exe --select-model none --for-indonesian
   ```

   **Source/manual ZIP users** use the private Python environment instead:

   ```powershell
   # Dedicated Indonesian model (recommended):
   .\.venv\Scripts\python.exe app/select_model.py turbo-id --for-indonesian

   # Smaller Indonesian model:
   .\.venv\Scripts\python.exe app/select_model.py small-id --for-indonesian

   # OR switch the shared default model:
   .\.venv\Scripts\python.exe app/select_model.py small
   .\.venv\Scripts\python.exe app/select_model.py large-v3
   ```

   Wait for `Selected ...` before continuing. First installation requires internet and can take time. The script selects the model only after the download succeeds; a failed download leaves your existing selection intact. Run the same command again to resume/reuse the download

4. Open `launchers/windows/Start Dictately.vbs`, choose English or Indonesian from the tray menu, click a text field, and press F12
5. Compare the same phrases with your previous model. Check names, numbers, negation, and the delay before text appears. A larger model is not guaranteed to win on every phrase

### Return to the default

Quit Dictately, then run:

```powershell
# Installer users
.\runtime\worker\DictatelyWorker.exe --select-model turbo

# Source/manual ZIP users, instead
.\.venv\Scripts\python.exe app/select_model.py turbo
```

Launch `launchers/windows/Start Dictately.vbs` again. This reuses/verifies the pinned Turbo download from setup. You do not need to rebuild Rust or reinstall the app to switch models.

## Where the files go

Models live beside the app in `models/whisper-turbo`, `models/whisper-turbo-id`, `models/whisper-small-id`, `models/whisper-small`, or `models/whisper-large-v3`. The choice is saved in `models/selected-model.json` (including optional `indonesian_model` for per-language routing), separate from transcript history. Optional downloads record their resolved Hugging Face revision in `source.json`; Turbo uses the checked-in checksum manifest. Models and selections are excluded from Git and release ZIPs.

Keep the selected folder intact. To free space, quit the app, switch to a different model successfully, then remove only the unused model folder. Setup continues to install/verify Turbo but does not reset an existing model selection. The old developer override `LOCALFLOW_MODEL=small` takes precedence; remove that environment variable if an unexpected Small model keeps loading.

## Improve recognition before changing models

1. Choose the correct fixed language before F12
2. Confirm Windows uses your intended microphone; check for clipping, very low volume, or noise
3. Add unusual names and technical terms in personal vocabulary
4. Enable the accuracy option in History & settings; it uses a wider decoding beam, which can increase processing time
5. Compare several real recordings in both languages rather than relying only on synthetic speech

## Other engines and custom models

Qwen3-ASR is a separate engine, **not** a drop-in model for faster-whisper. Its [official model card](https://huggingface.co/Qwen/Qwen3-ASR-0.6B) lists English and Indonesian support and describes its streaming backend. Dictately does not currently include that backend. Copying its weights into a Whisper folder will not work.

For contributors implementing another engine:

1. Use an isolated development checkout and Python environment
2. Add an engine adapter behind the worker protocol in `app/live_worker.py`, preserving `prepare`/recognition requests, explicit language locking, and local-only model loading
3. Map audio and results to the existing 16 kHz mono input and result fields, including timestamps/consumed audio for stable phrase output
4. Keep model download/setup separate from runtime and provide an explicit rollback to Turbo
5. Test phrase boundaries, English/Indonesian selection, silence, punctuation, and corrections before testing desktop insertion
6. Benchmark real speech accuracy, first-word delay, sustained latency, and peak VRAM on the target hardware before recommending it

The model-switch command intentionally accepts only the five compatible CTranslate2 Whisper options above (`turbo`, `turbo-id`, `small-id`, `small`, `large-v3`). See [development and testing](DEVELOPMENT.md) for test commands.
