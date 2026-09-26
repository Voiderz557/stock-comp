# PyInstaller spec for the AI-enabled paper-trading desktop window.
# Output: dist/StockCompDashboardAI/StockCompDashboard.exe
# Does not replace dist/StockCompDashboard/.

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH)

datas = []
binaries = []
hiddenimports = []

for package in ("streamlit", "altair", "webview"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

hiddenimports += collect_submodules("paper_trading")
hiddenimports += collect_submodules("strategies")
hiddenimports += collect_submodules("data")
hiddenimports += collect_submodules("market")
hiddenimports += collect_submodules("desktop")
hiddenimports += collect_submodules("ai")
hiddenimports += [
    "config",
    "app",
    "yfinance",
    "pyarrow",
    "pandas",
    "tornado",
    "watchdog",
    "webview",
    "webview.platforms.edgechromium",
    "streamlit.web.cli",
    "streamlit.web.bootstrap",
]

project_datas = [
    (str(ROOT / "app" / "competition_dashboard.py"), "app"),
    (str(ROOT / "config.py"), "."),
    (str(ROOT / "paper_trading"), "paper_trading"),
    (str(ROOT / "strategies"), "strategies"),
    (str(ROOT / "data"), "data"),
    (str(ROOT / "market"), "market"),
    (str(ROOT / "desktop"), "desktop"),
    (str(ROOT / "ai"), "ai"),
]
datas += project_datas

a = Analysis(
    [str(ROOT / "desktop" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "desktop" / "pyi_runtime_hook.py")],
    excludes=["benchmarking", "ml", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="StockCompDashboard",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="StockCompDashboardAI",
)
