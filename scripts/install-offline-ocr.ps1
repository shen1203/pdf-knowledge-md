param(
    [string]$PythonPath = ".\.venv\Scripts\python.exe",
    [string]$BundlePath = ".\offline-ocr"
)

$ErrorActionPreference = "Stop"
$bundle = [System.IO.Path]::GetFullPath($BundlePath)
$wheels = Join-Path $bundle "wheels"
$modelCache = Join-Path $bundle "model-cache"

if (-not (Test-Path -LiteralPath $wheels)) {
    throw "Offline wheel directory does not exist: $wheels"
}
if (-not (Test-Path -LiteralPath $modelCache)) {
    throw "Offline model cache does not exist: $modelCache"
}

& $PythonPath -m pip install `
    --no-index `
    --find-links $wheels `
    "paddlepaddle==3.3.1" `
    "paddleocr[doc-parser]==3.7.0"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$targetCache = Join-Path $env:USERPROFILE ".paddlex"
New-Item -ItemType Directory -Force -Path $targetCache | Out-Null
Copy-Item -Path (Join-Path $modelCache "*") -Destination $targetCache -Recurse -Force

& $PythonPath -c "import paddle; paddle.utils.run_check(); from paddleocr import PPStructureV3; print('PaddleOCR ready')"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Output "Offline OCR runtime and model cache installed."
