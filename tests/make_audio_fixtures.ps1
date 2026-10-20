$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
New-Item -ItemType Directory -Path "$taskRoot\qa" -Force | Out-Null
Add-Type -AssemblyName System.Speech
$voice=New-Object System.Speech.Synthesis.SpeechSynthesizer
$format=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono)
$phrases=@{
    'test-speech.wav'='I would like to send the report tomorrow.'
    'test-live.wav'='I would like to send the report tomorrow. Please include the latest numbers and send a copy to the whole team. We should check the final details before the meeting on Thursday. This is a test of live dictation on my laptop. The text should appear while I am still speaking.'
    'qa\correction-first.wav'='I would like to send the report on Friday.'
    'qa\correction-second.wav'='I would like to send the report on Monday.'
    'qa\language-english.wav'='English'
    'qa\language-indonesia.wav'='Indonesia'
    'qa\language-both.wav'='Both English and Indonesian'
}
try {
    foreach ($item in $phrases.GetEnumerator()) {
        $voice.SetOutputToWaveFile((Join-Path $taskRoot $item.Key),$format)
        $voice.Speak($item.Value)
        $voice.SetOutputToNull()
    }
} finally {$voice.Dispose()}
$taskPython=Join-Path $taskRoot '.venv\Scripts\python.exe'
Push-Location $taskRoot
try {
@'
import wave
from pathlib import Path
parts=[]
for name in ['correction-first.wav','correction-second.wav']:
    with wave.open(str(Path('qa')/name),'rb') as f:
        assert (f.getframerate(),f.getsampwidth(),f.getnchannels())==(16000,2,1)
        parts.append(f.readframes(f.getnframes()))
Path('qa/correction.pcm').write_bytes(parts[0]+bytes(64000)+parts[1]+bytes(32000))
'@ | & $taskPython -
    if ($LASTEXITCODE -ne 0) {throw 'Fixture assembly failed'}
} finally {Pop-Location}
