# Install SpeakerSplit (Windows, Python 3.11)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) {
    py -3.11 -m venv .venv
}

$py = ".\.venv\Scripts\python.exe"
& $py -m pip install -U pip -i https://pypi.tuna.tsinghua.edu.cn/simple

# CUDA 12.6 build of torch/torchaudio. pip 的默认源在 Windows 上会装成 CPU 版，
# 所以直接从阿里云镜像下 wheel 再本地安装。
$wheelDir = Join-Path $PSScriptRoot "_wheels"
New-Item -ItemType Directory -Force -Path $wheelDir | Out-Null
$base = "https://mirrors.aliyun.com/pytorch-wheels/cu126/"
$wheels = @(
    "torch-2.9.1+cu126-cp311-cp311-win_amd64.whl",
    "torchaudio-2.9.1+cu126-cp311-cp311-win_amd64.whl"
)
foreach ($name in $wheels) {
    $dst = Join-Path $wheelDir $name
    if (-not (Test-Path $dst)) {
        Write-Host "Downloading $name ..."
        Invoke-WebRequest -Uri ($base + [uri]::EscapeDataString($name)) -OutFile $dst
    }
}
& $py -m pip install --no-deps (Join-Path $wheelDir $wheels[0]) (Join-Path $wheelDir $wheels[1])

& $py -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

Write-Host "Done. Run: .\run_ui.bat (界面) 或 .\run.bat <文件> (命令行)"
