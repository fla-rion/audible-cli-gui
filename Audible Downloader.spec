# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['run_app.py'],
    pathex=[],
    binaries=[('/opt/homebrew/Cellar/ffmpeg/8.1.1/bin/ffmpeg', '.'), ('/opt/homebrew/Cellar/ffmpeg/8.1.1/bin/ffprobe', '.')],
    datas=[('templates', 'templates')],
    hiddenimports=['webview', 'webview.platforms.cocoa', 'jinja2', 'jinja2.ext', 'flask', 'flask.templating', 'audible', 'click', 'rich', 'httpx', 'h11', 'h2'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Audible Downloader',
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
    icon=['icon.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Audible Downloader',
)
app = BUNDLE(
    coll,
    name='Audible Downloader.app',
    icon='icon.icns',
    bundle_identifier='de.local.audible-downloader',
)
