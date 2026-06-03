"""
Instalador del protocolo URL firmador:// — variante PER-USER (sin Administrador)

A diferencia de install_protocol.py (que escribe en HKEY_CLASSES_ROOT y requiere
permisos de Administrador), este script registra el protocolo bajo
HKEY_CURRENT_USER\\Software\\Classes\\firmador, que NO requiere elevación.

Está pensado para el agente empaquetado como .exe (PyInstaller): el comando
registrado apunta directamente al ejecutable, no a python.exe.

Uso (NO requiere Administrador):
    python install_protocol_user.py                       # registra (busca AgenteFirma.exe junto al script o en .\dist)
    python install_protocol_user.py --exe "C:\\ruta\\AgenteFirma.exe"
    python install_protocol_user.py --remove              # desinstala

Nota: en producción el registro normalmente lo hace el instalador Inno Setup
(sección [Registry] con root: HKCU). Este script sirve para pruebas y para
registro manual sin reinstalar.
"""

import sys
from pathlib import Path

try:
    import winreg
except ImportError:
    print("ERROR: este script solo funciona en Windows.")
    sys.exit(1)


PROTOCOL = "firmador"
# Subclave per-user equivalente a HKEY_CLASSES_ROOT\firmador
BASE_SUBKEY = rf"Software\Classes\{PROTOCOL}"
_HERE = Path(__file__).parent.resolve()


def _find_exe() -> Path:
    """Resuelve la ruta del AgenteFirma.exe a registrar.

    Prioridad: --exe <ruta> > AgenteFirma.exe junto al script > .\\dist\\AgenteFirma.exe.
    """
    if "--exe" in sys.argv:
        idx = sys.argv.index("--exe")
        if idx + 1 >= len(sys.argv):
            print("ERROR: --exe requiere una ruta.")
            sys.exit(1)
        exe = Path(sys.argv[idx + 1]).resolve()
        if not exe.exists():
            print(f"ERROR: no existe el ejecutable indicado: {exe}")
            sys.exit(1)
        return exe

    candidates = [_HERE / "AgenteFirma.exe", _HERE / "dist" / "AgenteFirma.exe"]
    for c in candidates:
        if c.exists():
            return c.resolve()

    print(
        "ERROR: no se encontró AgenteFirma.exe.\n"
        "Empaquete primero con PyInstaller o pase la ruta con --exe.\n"
        f"Buscado en: {', '.join(str(c) for c in candidates)}"
    )
    sys.exit(1)


def _build_command(exe_path: Path) -> str:
    return f'"{exe_path}" "%1"'


def install() -> None:
    exe_path = _find_exe()
    command = _build_command(exe_path)

    # HKEY_CURRENT_USER\Software\Classes\firmador
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, BASE_SUBKEY) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f"URL:{PROTOCOL} Protocol")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")

    # HKEY_CURRENT_USER\Software\Classes\firmador\shell\open\command
    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER, rf"{BASE_SUBKEY}\shell\open\command"
    ) as cmd_key:
        winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, command)

    print(f"Protocolo '{PROTOCOL}://' registrado para el usuario actual (sin Administrador).")
    print(f"Comando registrado: {command}")
    print()
    print("Prueba en el navegador con:")
    print(f"  {PROTOCOL}://firmar?token=test")


def remove() -> None:
    try:
        winreg.DeleteKey(
            winreg.HKEY_CURRENT_USER, rf"{BASE_SUBKEY}\shell\open\command"
        )
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"{BASE_SUBKEY}\shell\open")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"{BASE_SUBKEY}\shell")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, BASE_SUBKEY)
        print(f"Protocolo '{PROTOCOL}://' eliminado del registro del usuario.")
    except FileNotFoundError:
        print(f"El protocolo '{PROTOCOL}://' no estaba registrado para este usuario.")


if __name__ == "__main__":
    if "--remove" in sys.argv:
        remove()
    else:
        install()
