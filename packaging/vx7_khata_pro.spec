# PyInstaller spec for VX7 KHATA PRO (one-folder build, windowed).
# Build from the project root:  pyinstaller packaging/vx7_khata_pro.spec --noconfirm
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent  # SPECPATH is provided by PyInstaller

# ReportLab imports many modules dynamically (e.g. reportlab.graphics.barcode.code128 via exec),
# which PyInstaller's import scan cannot see. Bundle them all explicitly.
hidden = collect_submodules("reportlab") + collect_submodules("openpyxl")

a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    datas=[(str(root / "vx7khata" / "assets"), "vx7khata/assets")],  # bundled fonts used by PDF export
    hiddenimports=hidden,
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VX7 KHATA PRO",
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="VX7 KHATA PRO")
