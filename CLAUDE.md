# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Digital signature service for PDF documents using a SafeNet eToken USB security token (PKCS#11). The core signing logic and Windows agent GUI are fully implemented. The REST API layer (`api.py`, `service.py`, `main.py`) is **planned but not yet implemented**.

**Token:** SafeNet eToken — DLL: `C:\Windows\System32\eTPKCS11.dll`  
**Private key CKA_ID:** auto-detected from the token at runtime (the agent enumerates certificates and picks a valid signing cert via `cert_validator.detect_signing_key_id`). Optionally overridden by `PRIVATE_KEY_ID` (hex) in `.env`.

## Commands

```bash
# First-time setup on a new PC
python -m venv venv
venv/Scripts/python.exe -m pip install -r requirements.txt

# IMPORTANT: always install packages with python -m pip, NOT pip.exe directly
# The venv has a path mismatch — pip.exe installs to a different site-packages
venv/Scripts/python.exe -m pip install <package>

# Sign a single PDF manually (requires token connected and .env configured)
venv/Scripts/python.exe -c "
from pathlib import Path
from pdf_signer import sign_pdf_file
sign_pdf_file(Path('input.pdf'), Path('output_firmado.pdf'), pin='TU_PIN')
"

# Register firmador:// protocol for the Python/dev flow (HKCR → python.exe + agent.py; requires Administrator)
venv/Scripts/python.exe install_protocol.py
venv/Scripts/python.exe install_protocol.py --remove

# Launch agent manually (normally invoked by Windows via the registered protocol).
# act_id (≤10 digits) and legajo (numeric, no length cap) are MANDATORY params;
# the agent rejects the URL without either.
venv/Scripts/python.exe agent.py "firmador://firmar?token=UUID&act_id=1234567890&legajo=12345678901"

# --- Packaging / distribution (see BUILD_INSTALADOR.md for the full flow) ---
# 1. Build the standalone agent .exe (onefile, --windowed)
venv/Scripts/pyinstaller --noconfirm AgenteFirma.spec          # → dist\AgenteFirma.exe

# 2. Compile the per-user installer with Inno Setup (ISCC may live under
#    %LOCALAPPDATA%\Programs\Inno Setup 6 or %ProgramFiles(x86)%\Inno Setup 6)
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" instalador.iss   # → Output\Instalador_AgenteFirma.exe

# Register the .exe flow per-user WITHOUT Administrator (HKCU; alternative to the installer, for manual/test PCs)
venv/Scripts/python.exe install_protocol_user.py --exe "dist\AgenteFirma.exe"
venv/Scripts/python.exe install_protocol_user.py --remove
```

> **What you distribute is `Output\Instalador_AgenteFirma.exe`, NOT `dist\AgenteFirma.exe`.**
> The bare `AgenteFirma.exe` is only the agent (the PIN window); it does **not**
> write any registry keys. The `firmador://` protocol is registered by the Inno
> Setup installer (or `install_protocol_user.py`). Copying just the `.exe` to
> another PC leaves nothing in regedit.

There is no automated test suite. Verify changes by signing a real PDF with the
token connected (the manual snippet above) and inspecting the output / `agent.log`.

## Architecture

```
.env → config.py → token_manager.py → pdf_signer.py
                                              ↑
                              cert_validator.py
                                              ↑
                           agent.py  (Windows GUI, firmador:// handler)
```

- **[config.py](config.py)** — Loads `.env` at import. The only hard failure is a missing PKCS#11 DLL (`FileNotFoundError`). `PRIVATE_KEY_ID` is **optional**: `None` when absent (triggering auto-detection), and only validated as hex (no `0x` prefix, raising `ValueError`) when present. Also exposes `SELF_BASE_URL`, `SOLICITUDES_PATH` (default `./pdfs_solicitudes`), and `FIRMADOS_PATH` (default `./pdfs_firmados`).
- **[token_manager.py](token_manager.py)** — `TokenSession(pin)` context manager; opens/closes PKCS#11 session via `python-pkcs11`. Raises `RuntimeError` if no token present, `ValueError` if PIN missing.
- **[pdf_signer.py](pdf_signer.py)** — `sign_pdf_file(input_path, output_path, pin)`: validates certificate, signs with pyHanko `PKCS11Signer`, and stamps a visible 250×72 pt box on the last page showing signer CN, timestamp, reason, and location. **Supports co-signing**: if the PDF already has signatures it adds a new one in a uniquely-named field (`Firma`, `Firma2`, `Firma3`…) so prior signatures stay valid, and tiles the visible stamp (bottom-right, stacking upward then into a new column) so boxes don't overlap. See "Visual Signature Stamp" below.
- **[cert_validator.py](cert_validator.py)** — `detect_signing_key_id(session)` enumerates the token's certificates and returns the CKA_ID of a valid (non-expired) signing certificate that has a matching private key. PKCS#11 allows only one active search per session, so it first collects all private-key CKA_IDs into a set, then iterates certificates matching against it (no nested `get_objects`). With multiple valid candidates it prefers signing-capable certs (`digital_signature`/`non_repudiation` KeyUsage; soft-accepted when KeyUsage is absent), then the furthest expiry. `validate_certificate(session, key_id)` checks expiry, optional SAN email, OCSP, and CRL — revocation is **soft-fail** on network errors, but a confirmed REVOKED (OCSP) or CRL-listed cert is a hard `ValueError`. Returns the parsed `x509.Certificate`. `get_cn_from_cert(cert)` extracts the CN for the stamp text.
- **[agent.py](agent.py)** — Tkinter GUI launched by Windows when `firmador://firmar?token=...` is opened. Runs signing in a background thread, marshals callbacks to the main thread via `root.after()`. Logs to `agent.log`.
- **[install_protocol.py](install_protocol.py)** — Writes `HKEY_CLASSES_ROOT\firmador` entries linking `firmador://` to `python.exe + agent.py`. **Requires Administrator** (HKCR). For the Python/dev flow.
- **[install_protocol_user.py](install_protocol_user.py)** — Per-user equivalent: writes `HKCU\Software\Classes\firmador` pointing directly at the packaged `AgenteFirma.exe`. **No Administrator needed**. For the `.exe` flow; the Inno Setup installer does the same registration automatically.

## Agent Signing Flow

```
Browser opens firmador://firmar?token=UUID&act_id=1234567890&legajo=12345678901
  → Windows launches: python agent.py "firmador://firmar?token=UUID&act_id=...&legajo=..."
  → _parse_params validates token + act_id (≤10 digits) + legajo (numeric, no cap) — ValueError → error dialog
  → AgentWindow (Tkinter) — PIN entry (shows act_id + legajo, "Mostrar PIN" toggle, Cancelar/Firmar)
  → background thread _sign_and_upload(token, pin, ..., act_id=act_id, legajo=legajo)
      ├─ [local mode]  GET {SELF_BASE_URL}/obtener-pdf/{token}  (returns PDF bytes; 404/409 handled)
      │                → sign_pdf_file(tmp_input, tmp_output, pin)
      │                → POST {SELF_BASE_URL}/subir-firmado/{token}  {"token", "act_id": int, "legajo": int, "pdf": <base64>}
      └─ [remote mode] POST pdf_url {"token": token}             → JSON {"url": "..."}
                       → _rebase_url(url, pdf_url)  (rewrite host to the endpoint's)
                       → GET <download_url>  (validates leading %PDF)
                       → sign_pdf_file(tmp_input, tmp_output, pin)
                       → POST upload_url {"token", "act_id": int, "legajo": int, "pdf": <base64>}
  → (both modes) save a local copy to FIRMADOS_PATH/{token}.pdf after upload
```

`act_id` (Nº de actuación) and `legajo` are **mandatory** query params parsed by `_parse_params`; both must be numeric — `act_id` ≤10 digits (DB `NUMERIC(10)`); `legajo` has no length cap (just numeric + present). Both are shown in the PIN window and sent as `int` in the upload payload of both modes. The agent only forwards `legajo`; it does not use it in the PDF signature itself.

**Remote mode** is triggered when the `firmador://` URL includes `pdf_url` and `upload_url` query parameters. The agent fetches the PDF over HTTP (POST → JSON `{"url"}` → GET); it does **not** read from disk. `SOLICITUDES_PATH` is currently unused by `agent.py` (it is loaded in `config.py` but reserved for the planned API). PKCS#11 errors are mapped to Spanish UI messages: `PinIncorrect`, `PinLocked` (PUK hint), and `TokenNotPresent`/`SlotIDInvalid` (token not detected); connection errors distinguish local vs. remote.

## Deployment

The agent runs on each user's PC (browsers cannot reach USB hardware directly).
It is distributed as a per-user installer, built in two steps on a dev PC:

- **[AgenteFirma.spec](AgenteFirma.spec)** — PyInstaller recipe → `dist\AgenteFirma.exe` (onefile, `--windowed`/no console). Uses `collect_all` for `pyhanko`, `pyhanko_certvalidator`, `pkcs11`, `asn1crypto`, `cryptography`, `oscrypto` (dynamic data/submodules). Add missing modules to `hiddenimports` if a packaged run raises `ModuleNotFoundError`.
- **[instalador.iss](instalador.iss)** — Inno Setup script → `Output\Instalador_AgenteFirma.exe`. Installs to `{userappdata}\AgenteFirma` with `PrivilegesRequired=lowest` (**no Administrator**), registers `firmador://` under `HKCU\Software\Classes\firmador`, and adds a Start Menu shortcut + uninstaller (`uninsdeletekey` removes the HKCU tree). The agent is launched **on demand by the browser via the protocol** — there is no login/startup shortcut, and it must **not** run as a Windows service (the PKCS#11 PIN dialog needs the user's interactive desktop, which `LocalSystem` lacks).

Per-PC prerequisite (NOT bundled): **SafeNet Authentication Client**, which provides `C:\Windows\System32\eTPKCS11.dll`. Without it the agent starts but `config.py` raises `FileNotFoundError`. See [BUILD_INSTALADOR.md](BUILD_INSTALADOR.md) and `ModoEjecucion.txt`.

## Configuration (.env)

```
PKCS11_LIB_PATH=C:\Windows\System32\eTPKCS11.dll
PRIVATE_KEY_ID=<hex, no 0x prefix, no spaces>   # OPTIONAL — overrides auto-detection; if absent the agent auto-detects the signing cert
SELF_BASE_URL=http://localhost:8000              # base URL for local-mode agent calls
FIRMADOS_PATH=C:\path\to\firmados                # optional; defaults to ./pdfs_firmados — where signed copies are saved
SOLICITUDES_PATH=C:\path\to\solicitudes          # optional; defaults to ./pdfs_solicitudes — currently unused by the agent (reserved for the planned API)
```

## Critical Library Distinction

Two PKCS#11 libraries are installed — they are NOT interchangeable:

| Library | Import | Role |
|---|---|---|
| `python-pkcs11` | `import pkcs11` | Used by pyHanko `PKCS11Signer` — **required** |
| `PyKCS11` | `from PyKCS11 import *` | Legacy, not used |

pyHanko's signer is at `pyhanko.sign.pkcs11.PKCS11Signer`.

## Visual Signature Stamp

`pdf_signer.py` reads the last page dimensions with `pypdf.PdfReader`, then places a `SigFieldSpec` box (250×72 pts, 10 pt margin) on that page via a `TextStampStyle` (font size 8, transparent background, 1 pt border). The stamp text template:

```
Firmado digitalmente por: %(signer)s
Fecha: %(ts)s
Motivo: %(reason)s
Lugar: %(location)s
```

`%(ts)s` is filled automatically by pyHanko. `%(signer)s` comes from `get_cn_from_cert(cert)`.

**Co-signing layout** — three helpers keep multiple signatures from clobbering each other:
- `_existing_sig_field_names(input_path)` enumerates existing signature fields (via `enumerate_sig_fields` on an `IncrementalPdfFileWriter`).
- `_unique_field_name(existing)` returns the next free field name — the first signature is `Firma` (original behavior), then `Firma2`, `Firma3`…
- `_stamp_box(page_w, page_h, n)` positions the visible box for signature *n* (0-based): `n == 0` is the bottom-right corner; higher *n* stack upward, then wrap into a new column to the left once a column fills.

Signing always uses an **incremental** write so previously-applied signatures remain cryptographically valid.

## Planned Components (not yet implemented)

- **`api.py`** — FastAPI server: `POST /firmar`, `GET /descargar/{id}`, `GET /estado/{id}`, `POST /crear-solicitud`, `GET /obtener-pdf/{token}`, `POST /subir-firmado/{token}`
- **`service.py`** — Background worker polling `pdfs_a_firmar/` for PDFs; owns shared `jobs` and `solicitudes` dicts; sends HTTP callbacks to Java/Tomcat (`CALLBACK_URL`)
- **`main.py`** — Entry point launching service worker thread + uvicorn server

When implemented, `api.py` will import shared state from `service.py` directly (in-process dicts, no message queue needed).

`Plan.txt` is the design spec for this FastAPI layer (Java/Tomcat callback flow). Its "Implementación completa" footer claims these files were built — that is aspirational; `api.py`/`service.py`/`main.py` are not present in the repo.

## Reference Docs

Design/history notes in the repo root — useful for context, but may be stale relative to the code:

- **`Firma.txt`** — earlier project layout (`pdfs_a_firmar/`, `service.py` loop) and notes on the `requirements.txt` cleanup.
- **`ModoEjecucion.txt`** — why a local agent is needed and how to package/distribute it (PyInstaller + Inno Setup, not a Windows service).
- **[BUILD_INSTALADOR.md](BUILD_INSTALADOR.md)** — step-by-step build of `Output\Instalador_AgenteFirma.exe` (PyInstaller → Inno Setup), `--onefile` caveats (`agent.log` / local copies land in a temp dir), and the manual per-user registration fallback. This doc is current with the code.
- **`Plan.txt`** — full design for the planned FastAPI integration with Java/Tomcat.
