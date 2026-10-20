# Local AI models: choose, install, and switch

Dictately runs speech recognition **locally on your computer**. These options do not use a cloud transcription API, require an API key, or charge per minute. Internet access is needed to download dependencies and model files; afterward the worker loads local files only. Electricity, storage, and hardware are your own costs.

## Which model should I choose?

| Option | Use it for | Tradeoff |
| --- | --- | --- |
| `turbo`: Whisper large-v3-turbo | Default everyday dictation, especially with an NVIDIA GPU | A practical speed/accuracy balance; approximately 1.6 GB weight download |
| `small`: Whisper Small multilingual | Lower memory use or trying CPU-only dictation | Smaller model, but recognition can be less accurate; not the English-only `small.en` model |
| `large-v3`: full Whisper large-v3 | Testing whether a larger decoder reduces mistakes on your voice | More storage, memory, and processing; live output may lag more |

All three use the same Windows **English / Indonesian / Both** controls, F10 voice/mouse picker, configurable F12/F10 defaults, local history, corrections, and destination protection. Both constrains per-segment detection to English/Indonesian; installing a model does not add other supported languages.

Turbo is a pruned version of Large-v3 with 4 decoder layers instead of 32, trading some quality for speed: see the [Turbo model card](https://huggingface.co/openai/whisper-large-v3-turbo). The optional [Small](https://huggingface.co/Systran/faster-whisper-small) and [Large-v3](https://huggingface.co/Systran/faster-whisper-large-v3) packages are CTranslate2 conversions compatible with the existing worker.

On the development laptop (RTX 3050 6 GB, about 16 GB system RAM), Turbo is the tested default. Large-v3 and Small are selectable alternatives, not claims of measured improvements on that device. Available GPU memory, other running apps, power mode, and your audio affect results. The optional models have not received the same real-device recognition testing as Turbo.

## Step by step: install a different model

1. Complete the normal [installation](../README.md#download-and-install) first. Keep the app in its permanent folder
2. Stop recording, then choose **Quit** in the tray menu, or run the following in PowerShell from the app folder

   ```powershell
   & '.\scripts/windows/Stop Dictately.ps1'
   ```

3. Download and select **one** model. **Installer users** use the included worker:

   ```powershell
   # Choose one
   .\runtime\worker\DictatelyWorker.exe --select-model small
   .\runtime\worker\DictatelyWorker.exe --select-model large-v3
   ```

   **Source/manual ZIP users** use the private Python environment instead:

   ```powershell
   # Smaller model
   .\.venv\Scripts\python.exe app/select_model.py small

   # OR full Large-v3
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

Models live beside the app in `models/whisper-turbo`, `models/whisper-small`, or `models/whisper-large-v3`. The choice is saved in `models/selected-model.json`, separate from transcript history. Optional downloads record their resolved Hugging Face revision in `source.json`; Turbo uses the checked-in checksum manifest. Models and selections are excluded from Git and release ZIPs.

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

The model-switch command intentionally accepts only the three compatible Whisper options above. See [development and testing](DEVELOPMENT.md) for test commands.
