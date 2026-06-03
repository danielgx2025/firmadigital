import pkcs11
import config


class TokenSession:
    """
    Context manager que abre y cierra la sesión PKCS#11 con el token USB.
    Usa python-pkcs11 (requerido por pyHanko PKCS11Signer).
    """

    def __init__(self, pin: str = None):
        self._pin = pin
        self._lib = None
        self._session_ctx = None
        self._session = None

    def __enter__(self):
        self._lib = pkcs11.lib(config.PKCS11_LIB_PATH)
        slots = list(self._lib.get_slots(token_present=True))
        if not slots:
            raise RuntimeError("No se encontró ningún token PKCS#11 conectado.")
        token = slots[0].get_token()
        if not self._pin:
            raise ValueError("Se requiere un PIN para abrir la sesión del token.")
        self._session_ctx = token.open(user_pin=self._pin)
        self._session = self._session_ctx.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._session_ctx:
            self._session_ctx.__exit__(exc_type, exc_val, exc_tb)
        return False

    @property
    def session(self):
        return self._session
