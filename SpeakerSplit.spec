# -*- mode: python ; coding: utf-8 -*-
import ast
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


def _scan_imports(root: Path) -> set:
    """扫一遍 torch 源码里的 import，补进 hiddenimports。

    torch 不在包里，PyInstaller 分析不到它的依赖（pickletools、unittest.mock
    这类标准库子模块最容易漏），漏掉运行时会 ModuleNotFoundError。
    这里连子模块名一起收集，例如 unittest.mock。
    """
    names: set = set()
    if not root.is_dir():
        return names
    stdlib = set(sys.stdlib_module_names)

    def add(module: str) -> None:
        top = module.split(".")[0]
        if top in _SKIP or top not in stdlib:
            return
        names.add(module)
        names.add(top)

    for py in root.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                add(node.module)
    return names


_RUNTIME = Path(SPECPATH) / "_stage" / "cpu"      # noqa: F821
_SKIP = {"torch", "torchaudio", "torchgen", "functorch", "torchvision", "__future__"}
_TORCH_DEPS = ["filelock", "typing_extensions", "sympy", "mpmath", "networkx",
               "jinja2", "markupsafe", "fsspec", "setuptools", "numpy", "dill",
               "yaml", "tqdm"]
_extra = sorted(_scan_imports(_RUNTIME))

datas = [('assets/app.ico', 'assets'), ('assets/icon.png', 'assets')]
binaries = []
hiddenimports = (['app.diarize_cli'] + _extra + _TORCH_DEPS)
# torch / torchaudio 不进包：CPU 版随安装包放在 runtimes/cpu，GPU 版首次启动再下载，
# 打进包里的 CUDA 库有 3.4 GB，而 PyInstaller 的 FrozenImporter 会盖住外部目录
tmp_ret = collect_all('faster_whisper')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('tokenizers')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('ctranslate2')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('sherpa_onnx')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('audio_separator')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('librosa')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('soundfile')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('av')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('onnxruntime')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('demucs')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('julius')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('lameenc')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('openunmix')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('sphn')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'tkinter', 'IPython',
              'torch', 'torchaudio', 'torchvision', 'torchgen', 'torchtext'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SpeakerSplit',
    icon='assets/app.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SpeakerSplit',
)
