# PyInstaller spec — produces a single-file executable
# Build: pyinstaller drewq.spec

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=[str(Path(".").resolve())],
    binaries=[],
    datas=[
        ("assets", "assets"),
    ],
    hiddenimports=[
        "pystray._darwin",       # macOS tray backend
        "pystray._win32",        # Windows tray backend
        "pystray._xorg",         # Linux tray backend
        "PIL._tkinter_finder",
        "tkinter",
        "tkinter.ttk",
        "smartcard",
        "smartcard.System",
        "smartcard.pcsc",
        "Crypto.Cipher.DES",
        "Crypto.Cipher.DES3",
        "websockets",
        "websockets.legacy",
        "websockets.legacy.client",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="DREWQ Reader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,           # No terminal window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Windows icon
    icon="assets/icon.ico" if sys.platform == "win32" else None,
)

# macOS: wrap in .app bundle
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="DREWQ Reader.app",
        icon="assets/icon.icns",
        bundle_identifier="com.drewq.reader",
        info_plist={
            "NSPrincipalClass": "NSApplication",
            "NSHighResolutionCapable": True,
            "LSUIElement": True,          # Hide from dock (tray-only app)
            "CFBundleShortVersionString": "1.0.0",
        },
    )
