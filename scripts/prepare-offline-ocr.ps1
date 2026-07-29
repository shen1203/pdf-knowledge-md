param(
    [string]$PythonPath = ".\.venv\Scripts\python.exe",
    [string]$BundlePath = ".\offline-ocr"
)

$ErrorActionPreference = "Stop"
$bundle = [System.IO.Path]::GetFullPath($BundlePath)
$wheels = Join-Path $bundle "wheels"
$modelCache = Join-Path $bundle "model-cache"

New-Item -ItemType Directory -Force -Path $wheels | Out-Null

& $PythonPath -m pip download `
    --dest $wheels `
    "paddlepaddle==3.3.1"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $PythonPath -m pip download `
    --dest $wheels `
    "paddleocr[doc-parser]==3.7.0"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $PythonPath -m pip install `
    "paddlepaddle==3.3.1" `
    "paddleocr[doc-parser]==3.7.0"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $PythonPath ".\scripts\warmup_ocr.py"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$sourceCache = Join-Path $env:USERPROFILE ".paddlex"
if (-not (Test-Path -LiteralPath $sourceCache)) {
    throw "PaddleX model cache was not created at $sourceCache"
}
New-Item -ItemType Directory -Force -Path $modelCache | Out-Null
Copy-Item -Path (Join-Path $sourceCache "*") -Destination $modelCache -Recurse -Force

Write-Output "Offline OCR bundle created at: $bundle"
