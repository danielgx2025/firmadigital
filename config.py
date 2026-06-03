import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

PKCS11_LIB_PATH: str = os.getenv("PKCS11_LIB_PATH", r"C:\Windows\System32\eTPKCS11.dll")
SELF_BASE_URL: str = os.getenv("SELF_BASE_URL", "http://localhost:8000")

_solicitudes_raw = os.getenv("SOLICITUDES_PATH", "")
SOLICITUDES_PATH: Path = Path(_solicitudes_raw) if _solicitudes_raw else BASE_DIR / "pdfs_solicitudes"

_firmados_raw = os.getenv("FIRMADOS_PATH", "")
FIRMADOS_PATH: Path = Path(_firmados_raw) if _firmados_raw else BASE_DIR / "pdfs_firmados"

# PRIVATE_KEY_ID es opcional: override del CKA_ID a usar. Si está vacío/ausente,
# el agente auto-detecta el certificado de firma vigente en el token (ver
# cert_validator.detect_signing_key_id), permitiendo usar el mismo agente en
# distintas PCs con tokens distintos.
_key_id_hex = os.getenv("PRIVATE_KEY_ID", "").strip()
if _key_id_hex:
    try:
        PRIVATE_KEY_ID: bytes | None = bytes.fromhex(_key_id_hex)
    except ValueError:
        raise ValueError(f"PRIVATE_KEY_ID en .env no es un hex válido: '{_key_id_hex}'")
else:
    PRIVATE_KEY_ID: bytes | None = None

if not os.path.exists(PKCS11_LIB_PATH):
    raise FileNotFoundError(f"Librería PKCS#11 no encontrada: {PKCS11_LIB_PATH}")
