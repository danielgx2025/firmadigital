"""
Instalador del protocolo URL firmador://

Registra en el Registro de Windows la asociación entre el esquema
de URL 'firmador://' y este proyecto, de modo que el navegador abra
agent.py automáticamente cuando el sistema judicial envíe esa URL.

Uso (requiere permisos de Administrador):
    python install_protocol.py           # instala
    python install_protocol.py --remove  # desinstala
"""

import os
import sys
from pathlib import Path

try:
    import winreg
except ImportError:
    print("ERROR: este script solo funciona en Windows.")
    sys.exit(1)


PROTOCOL = "firmador"
_HERE = Path(__file__).parent.resolve()
AGENT_PATH = _HERE / "agent.py"


def _build_command() -> str:
    python_exe = Path(sys.executable).resolve()
    return f'"{python_exe}" "{AGENT_PATH}" "%1"'


def install() -> None:
    command = _build_command()

    try:
        # HKEY_CLASSES_ROOT\firmador
        with winreg.CreateKey(winreg.HKEY_CLASSES_ROOT, PROTOCOL) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f"URL:{PROTOCOL} Protocol")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")

        # HKEY_CLASSES_ROOT\firmador\shell\open\command
        with winreg.CreateKey(
            winreg.HKEY_CLASSES_ROOT, rf"{PROTOCOL}\shell\open\command"
        ) as cmd_key:
            winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, command)

    except PermissionError:
        print(
            "ERROR: Se requieren permisos de Administrador.\n"
            "Ejecute este script con 'Ejecutar como administrador'."
        )
        sys.exit(1)

    print(f"Protocolo '{PROTOCOL}://' registrado exitosamente.")
    print(f"Comando registrado: {command}")
    print()
    print("Prueba en el navegador con:")
    print(f"  {PROTOCOL}://firmar?token=test")


def remove() -> None:
    try:
        winreg.DeleteKey(
            winreg.HKEY_CLASSES_ROOT, rf"{PROTOCOL}\shell\open\command"
        )
        winreg.DeleteKey(winreg.HKEY_CLASSES_ROOT, rf"{PROTOCOL}\shell\open")
        winreg.DeleteKey(winreg.HKEY_CLASSES_ROOT, rf"{PROTOCOL}\shell")
        winreg.DeleteKey(winreg.HKEY_CLASSES_ROOT, PROTOCOL)
        print(f"Protocolo '{PROTOCOL}://' eliminado del registro.")
    except FileNotFoundError:
        print(f"El protocolo '{PROTOCOL}://' no estaba registrado.")
    except PermissionError:
        print(
            "ERROR: Se requieren permisos de Administrador.\n"
            "Ejecute este script con 'Ejecutar como administrador'."
        )
        sys.exit(1)


if __name__ == "__main__":
    if "--remove" in sys.argv:
        remove()
    else:
        install()
