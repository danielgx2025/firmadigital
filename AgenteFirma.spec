# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec para el Agente de Firma (agent.py).

Genera un único AgenteFirma.exe (onefile, sin consola). pyHanko y sus
dependencias usan datos/imports dinámicos, por eso se recolectan con
collect_all en lugar de depender de la autodetección.

Compilar:
    venv\\Scripts\\pyinstaller --noconfirm AgenteFirma.spec

Salida: dist\\AgenteFirma.exe
"""

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# Paquetes con datos/submódulos cargados dinámicamente.
for _pkg in (
    "pyhanko",
    "pyhanko_certvalidator",
    "pkcs11",
    "asn1crypto",
    "cryptography",
    "oscrypto",
):
    try:
        _d, _b, _h = collect_all(_pkg)
        datas += _d
        binaries += _b
        hiddenimports += _h
    except Exception:
        # Si algún paquete opcional no está instalado, se ignora.
        pass

# Imports que a veces no se detectan por estar referenciados de forma indirecta.
hiddenimports += [
    "requests",
    "dotenv",
    "pypdf",
    "tkinter",
]


a = Analysis(
    ["agent.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AgenteFirma",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # --windowed: sin ventana de consola
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="firma.ico",     # opcional: descomentar si se agrega un icono
)
