$ErrorActionPreference = 'Stop'
$modelDirectory = Join-Path $PSScriptRoot '..\models\kokoro'
New-Item -ItemType Directory -Force -Path $modelDirectory | Out-Null

$modelUrl = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.int8.onnx'
$voicesUrl = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin'
Invoke-WebRequest -Uri $modelUrl -OutFile (Join-Path $modelDirectory 'kokoro-v1.0.int8.onnx')
Invoke-WebRequest -Uri $voicesUrl -OutFile (Join-Path $modelDirectory 'voices-v1.0.bin')
Get-ChildItem $modelDirectory | Select-Object Name, Length
