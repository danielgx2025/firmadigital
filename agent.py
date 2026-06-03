"""
Agente de firma digital — protocolo firmador://

Uso (flujo local):
    python agent.py "firmador://firmar?token=UUID"

Uso (flujo directo, sin localhost):
    python agent.py "firmador://firmar?token=UUID&pdf_url=http://servidor/pdf/UUID&upload_url=http://servidor/firmado/UUID"

Windows ejecuta este script automáticamente cuando el navegador abre
una URL del tipo firmador://firmar?token=XXXX, siempre que el protocolo
esté registrado (ver install_protocol.py).
"""

import base64
import logging
import os
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlunparse

# Asegurar que el directorio del script esté en el path y sea el CWD
_HERE = Path(__file__).parent.resolve()
os.chdir(_HERE)
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

_log_path = _HERE / "agent.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(_log_path, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("agent")

import tkinter as tk
from tkinter import font as tkfont

import pkcs11.exceptions
import requests

import config
from pdf_signer import sign_pdf_file



def _parse_params(url: str) -> dict:
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    tokens = params.get("token", [])
    if not tokens:
        raise ValueError(f"URL sin parámetro 'token': {url}")
    result = {"token": tokens[0]}
    if params.get("pdf_url"):
        result["pdf_url"] = params["pdf_url"][0]
    if params.get("upload_url"):
        result["upload_url"] = params["upload_url"][0]
    return result


def _rebase_url(target_url: str, base_url: str) -> str:
    """Combina scheme+host+puerto de base_url con el path/query de target_url.
    Útil cuando el server devuelve un host 'localhost' no alcanzable desde la PC."""
    t = urlparse(target_url)
    b = urlparse(base_url)
    return urlunparse((b.scheme, b.netloc, t.path, t.params, t.query, t.fragment))


def _sign_and_upload(
    token: str,
    pin: str,
    on_progress,
    on_success,
    on_error,
    *,
    pdf_url: str = None,
    upload_url: str = None,
) -> None:
    """Ejecutado en un hilo secundario para no bloquear la GUI."""
    log.info(f"Inicio firma: token={token}")
    tmp_input = None
    tmp_output = None
    effective_pdf_url = pdf_url or f"{config.SELF_BASE_URL}/obtener-pdf/{token}"
    effective_upload_url = upload_url or f"{config.SELF_BASE_URL}/subir-firmado/{token}"
    try:
        on_progress("Descargando documento...")
        if pdf_url:
            log.info(f"POST {pdf_url} con token={token}")
            resp = requests.post(pdf_url, json={"token": token}, timeout=30)
            log.info(f"POST {pdf_url} → {resp.status_code}")
            if resp.status_code == 404:
                log.error("Solicitud no encontrada (404)")
                on_error("Solicitud no encontrada o expirada.")
                return
            resp.raise_for_status()

            # El endpoint devuelve JSON: {"url": ".../static/solicitudes/<token>.pdf"}
            try:
                data = resp.json()
            except ValueError:
                raise ValueError(
                    f"El endpoint no devolvió JSON válido. Respuesta: {resp.text[:200]!r}"
                )
            raw_url = data.get("url")
            if not raw_url:
                raise ValueError(f"El endpoint no devolvió el campo 'url'. JSON: {data!r}")
            log.info(f"URL del PDF devuelta por el endpoint: {raw_url}")

            # El server puede devolver host 'localhost'; reescribir al host del endpoint
            download_url = _rebase_url(raw_url, pdf_url)
            log.info(f"Descargando PDF desde: {download_url}")
            dl = requests.get(download_url, timeout=30)
            log.info(f"GET {download_url} → {dl.status_code}")
            if dl.status_code == 404:
                log.error("PDF no encontrado en la URL (404)")
                on_error("El servidor no encontró el PDF solicitado.")
                return
            dl.raise_for_status()
            pdf_bytes = dl.content
            log.info(f"PDF descargado desde URL ({len(pdf_bytes)} bytes)")
            if not pdf_bytes.startswith(b'%PDF'):
                raise ValueError(
                    f"La URL no devolvió un PDF válido. Primeros bytes: {pdf_bytes[:40]!r}"
                )
        else:
            log.info(f"GET {effective_pdf_url}")
            resp = requests.get(effective_pdf_url, timeout=30)
            log.info(f"GET {effective_pdf_url} → {resp.status_code}")
            if resp.status_code == 404:
                log.error("Solicitud no encontrada (404)")
                on_error("Solicitud no encontrada o expirada.")
                return
            if resp.status_code == 409:
                log.error("Solicitud ya procesada (409)")
                on_error("Esta solicitud ya fue procesada.")
                return
            resp.raise_for_status()
            pdf_bytes = resp.content
            log.info(f"Primeros bytes PDF (local): {pdf_bytes[:20]!r}")
            if not pdf_bytes.startswith(b'%PDF'):
                raise ValueError(
                    f"El servidor no devolvió un PDF válido. Primeros bytes: {pdf_bytes[:40]!r}"
                )
            log.info(f"PDF descargado vía HTTP ({len(pdf_bytes)} bytes)")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            tmp_input = Path(f.name)

        tmp_output = tmp_input.with_suffix(".firmado.pdf")

        on_progress("Firmando con el token USB...")
        log.info(f"Firmando PDF: {tmp_input}")
        sign_pdf_file(tmp_input, tmp_output, pin=pin)
        log.info(f"PDF firmado: {tmp_output}")

        on_progress("Subiendo documento firmado...")
        log.info(f"POST {effective_upload_url}")
        pdf_firmado_bytes = tmp_output.read_bytes()
        pdf_b64 = base64.b64encode(pdf_firmado_bytes).decode("utf-8")
        upload = requests.post(
            effective_upload_url,
            json={"token": token, "pdf": pdf_b64},
            timeout=60,
        )
        log.info(f"Subida → {upload.status_code}")
        upload.raise_for_status()

        try:
            config.FIRMADOS_PATH.mkdir(parents=True, exist_ok=True)
            dest = config.FIRMADOS_PATH / f"{token}.pdf"
            dest.write_bytes(pdf_firmado_bytes)
            log.info(f"Copia local guardada: {dest}")
        except Exception as save_err:
            log.warning(f"No se pudo guardar copia local: {save_err}")

        log.info("Proceso completado exitosamente")
        on_success()

    except requests.exceptions.ConnectionError:
        is_local = "localhost" in effective_pdf_url or "127.0.0.1" in effective_pdf_url
        if is_local:
            log.error("ConnectionError: no se puede conectar al servicio local")
            on_error("No se puede conectar al servicio de firma local.\nVerifique que el servicio esté iniciado.")
        else:
            log.error("ConnectionError: no se puede conectar al servidor")
            on_error("No se puede conectar al servidor.\nVerifique su conexión e intente nuevamente.")
    except pkcs11.exceptions.PinIncorrect:
        log.error("PIN incorrecto")
        on_error("PIN incorrecto. Intente nuevamente.")
    except pkcs11.exceptions.PinLocked:
        log.error("Token bloqueado por intentos fallidos")
        on_error(
            "Token bloqueado por demasiados intentos incorrectos.\n"
            "Use la utilidad SafeNet Authentication Client para desbloquearlo con el PUK."
        )
    except (pkcs11.exceptions.TokenNotPresent, pkcs11.exceptions.SlotIDInvalid):
        log.error("Token USB no detectado")
        on_error("Token USB no detectado.\nConecte el token e intente nuevamente.")
    except Exception as e:
        log.error(f"Excepción inesperada: {e}", exc_info=True)
        on_error(f"Error: {e}")
    finally:
        for p in (tmp_input, tmp_output):
            if p and p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass


class AgentWindow:
    def __init__(self, root: tk.Tk, token: str, pdf_url: str = None, upload_url: str = None):
        self.root = root
        self.token = token
        self.pdf_url = pdf_url
        self.upload_url = upload_url
        self._build_ui()

    def _build_ui(self):
        self.root.title("Firmar Documento Digital")
        self.root.resizable(False, False)
        self.root.geometry("380x220")
        self.root.configure(bg="#f5f5f5")

        try:
            self.root.iconbitmap(default="")
        except Exception:
            pass

        bold = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        normal = tkfont.Font(family="Segoe UI", size=9)

        tk.Label(
            self.root,
            text="Firma Digital — Poder Judicial de Salta",
            font=bold,
            bg="#f5f5f5",
            fg="#333",
        ).pack(pady=(18, 2))

        tk.Label(
            self.root,
            text="Ingrese el PIN del token USB:",
            font=normal,
            bg="#f5f5f5",
            fg="#555",
        ).pack(pady=(10, 2))

        self._pin_var = tk.StringVar()
        pin_entry = tk.Entry(
            self.root,
            textvariable=self._pin_var,
            show="*",
            font=normal,
            width=20,
            relief="solid",
            bd=1,
        )
        pin_entry.pack(pady=(0, 10))
        pin_entry.focus_set()
        pin_entry.bind("<Return>", lambda _: self._on_firmar())

        self._btn_firmar = tk.Button(
            self.root,
            text="Firmar",
            command=self._on_firmar,
            font=bold,
            bg="#1a6faf",
            fg="white",
            relief="flat",
            padx=20,
            pady=6,
            cursor="hand2",
            activebackground="#155a8a",
            activeforeground="white",
        )
        self._btn_firmar.pack()

        self._status_var = tk.StringVar()
        self._status_label = tk.Label(
            self.root,
            textvariable=self._status_var,
            font=normal,
            bg="#f5f5f5",
            fg="#555",
            wraplength=340,
        )
        self._status_label.pack(pady=(12, 0))

    def _set_status(self, msg: str, color: str = "#555"):
        self._status_var.set(msg)
        self._status_label.configure(fg=color)
        self.root.update_idletasks()

    def _on_firmar(self):
        pin = self._pin_var.get().strip()
        if not pin:
            self._set_status("Debe ingresar el PIN.", "#c0392b")
            return

        self._btn_firmar.configure(state="disabled")
        self._set_status("Procesando...", "#555")

        threading.Thread(
            target=_sign_and_upload,
            args=(
                self.token,
                pin,
                lambda msg: self.root.after(0, self._set_status, msg, "#555"),
                lambda: self.root.after(0, self._on_success),
                lambda msg: self.root.after(0, self._on_error, msg),
            ),
            kwargs={"pdf_url": self.pdf_url, "upload_url": self.upload_url},
            daemon=True,
        ).start()

    def _on_success(self):
        self._set_status("Documento firmado exitosamente.", "#27ae60")
        self.root.after(2000, self.root.destroy)

    def _on_error(self, msg: str):
        self._set_status(msg, "#c0392b")
        self._btn_firmar.configure(state="normal")


def main():
    if len(sys.argv) < 2:
        # Lanzado sin URL: mostrar mensaje de ayuda
        root = tk.Tk()
        root.withdraw()
        import tkinter.messagebox as mb
        mb.showerror(
            "Agente de Firma",
            "Este programa es invocado automáticamente por el navegador.\n"
            "No es necesario ejecutarlo manualmente.",
        )
        return

    log.info(f"Agente iniciado: {sys.argv[1]}")
    try:
        params = _parse_params(sys.argv[1])
    except ValueError as e:
        log.error(f"URL inválida: {e}")
        root = tk.Tk()
        root.withdraw()
        import tkinter.messagebox as mb
        mb.showerror("Agente de Firma", f"URL inválida: {e}")
        return

    log.info(f"Parámetros: token={params['token']} pdf_url={params.get('pdf_url')} upload_url={params.get('upload_url')}")
    root = tk.Tk()
    AgentWindow(
        root,
        token=params["token"],
        pdf_url=params.get("pdf_url"),
        upload_url=params.get("upload_url"),
    )
    root.mainloop()


if __name__ == "__main__":
    main()
