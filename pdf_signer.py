import logging
from pathlib import Path

import pypdf
from pyhanko.sign.pkcs11 import PKCS11Signer
from pyhanko.sign import signers, fields as sig_fields
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.stamp.text import TextStampStyle, TextBoxStyle
from pyhanko.pdf_utils.layout import SimpleBoxLayoutRule, AxisAlignment, Margins

import config
from cert_validator import (
    validate_certificate,
    get_cn_from_cert,
    get_email_from_cert,
    detect_signing_key_id,
)
from token_manager import TokenSession

log = logging.getLogger(__name__)

_STAMP_TEXT = (
    "Firmado digitalmente por: %(signer)s\n"
    "Fecha: %(ts)s\n"
    "Email: %(email)s\n"
    "Lugar: %(location)s"
)
_STAMP_FONT_SIZE = 8
_STAMP_PAD_V = 4   # padding interno arriba/abajo (pt)
_STAMP_PAD_H = 8   # padding interno izquierda/derecha (pt)
_STAMP_W = 250
_STAMP_LINES = _STAMP_TEXT.count("\n") + 1            # 4 líneas
_STAMP_H = _STAMP_LINES * _STAMP_FONT_SIZE + 2 * _STAMP_PAD_V   # 4*8 + 8 = 40 pt
_STAMP_MARGIN = 10
_STAMP_GAP = 6  # separación entre sellos apilados


def _last_page_info(input_path: Path) -> tuple[int, float, float]:
    """Returns (last_page_index, page_width_pts, page_height_pts)."""
    with open(input_path, "rb") as f:
        reader = pypdf.PdfReader(f)
        n = len(reader.pages)
        page = reader.pages[n - 1]
        w = float(page.mediabox.width)
        h = float(page.mediabox.height)
    return n - 1, w, h


def _existing_sig_field_names(input_path: Path) -> list[str]:
    """Nombres de los campos de firma ya presentes en el PDF."""
    with open(input_path, "rb") as f:
        w = IncrementalPdfFileWriter(f, strict=False)
        return [name for name, _val, _ref in sig_fields.enumerate_sig_fields(w)]


def _unique_field_name(existing: set[str], base: str = "Firma") -> str:
    """Devuelve un nombre de campo de firma libre: 'Firma', 'Firma2', 'Firma3'..."""
    if base not in existing:
        return base  # primera firma -> "Firma" (comportamiento original)
    i = 2
    while f"{base}{i}" in existing:
        i += 1
    return f"{base}{i}"


def _stamp_box(page_w: float, page_h: float, n: int) -> tuple[float, float, float, float]:
    """Recuadro del sello visible para la firma nº n (0-based).

    Con n == 0 coincide con la esquina inferior derecha (un solo firmante).
    Para n > 0 apila hacia arriba y, si no entra, salta a una columna a la izquierda.
    """
    usable_h = page_h - 2 * _STAMP_MARGIN
    per_col = max(1, int((usable_h + _STAMP_GAP) // (_STAMP_H + _STAMP_GAP)))
    col, row = divmod(n, per_col)
    x1 = page_w - _STAMP_MARGIN - col * (_STAMP_W + _STAMP_GAP)
    x0 = x1 - _STAMP_W
    y0 = _STAMP_MARGIN + row * (_STAMP_H + _STAMP_GAP)
    y1 = y0 + _STAMP_H
    return (x0, y0, x1, y1)


def sign_pdf_file(input_path: Path, output_path: Path, pin: str = None) -> None:
    """Firma digitalmente un PDF usando el token USB y lo guarda en output_path.

    Soporta co-firma: si el PDF ya tiene firmas, se agrega una nueva en un campo
    de nombre único (Firma2, Firma3...) manteniendo válidas las anteriores, con el
    sello visible apilado para que no se solape.
    """
    last_page, page_w, page_h = _last_page_info(input_path)

    existing = _existing_sig_field_names(input_path)
    field_name = _unique_field_name(set(existing))
    stamp_box = _stamp_box(page_w, page_h, len(existing))
    log.info(f"Firmando campo '{field_name}' (firmas previas: {len(existing)})")

    with TokenSession(pin=pin) as tok:
        # Usa el ID configurado en .env si existe; si no, auto-detecta el
        # certificado de firma vigente del token (funciona en cualquier PC).
        key_id = config.PRIVATE_KEY_ID or detect_signing_key_id(tok.session)
        cert = validate_certificate(tok.session, key_id)
        signer_name = get_cn_from_cert(cert)
        signer_email = get_email_from_cert(cert) or "Documento firmado digitalmente"

        pkcs11_signer = PKCS11Signer(
            pkcs11_session=tok.session,
            key_id=key_id,
            cert_id=key_id,
        )

        sig_meta = signers.PdfSignatureMetadata(
            field_name=field_name,
            reason=signer_email,
            location="Salta, Argentina",
        )

        field_spec = sig_fields.SigFieldSpec(
            sig_field_name=field_name,
            on_page=last_page,
            box=stamp_box,
        )

        stamp_style = TextStampStyle(
            stamp_text=_STAMP_TEXT,
            text_box_style=TextBoxStyle(
                font_size=_STAMP_FONT_SIZE,
                box_layout_rule=SimpleBoxLayoutRule(
                    x_align=AxisAlignment.ALIGN_MID,
                    y_align=AxisAlignment.ALIGN_MID,
                    margins=Margins(
                        left=_STAMP_PAD_H, right=_STAMP_PAD_H,
                        top=_STAMP_PAD_V, bottom=_STAMP_PAD_V,
                    ),
                ),
            ),
            background_opacity=0,
            border_width=1,
        )

        pdf_signer = signers.PdfSigner(
            signature_meta=sig_meta,
            signer=pkcs11_signer,
            stamp_style=stamp_style,
            new_field_spec=field_spec,
        )

        appearance_params = {
            "signer": signer_name,
            "email": signer_email,
            "location": "Salta, Argentina",
        }

        with open(input_path, "rb") as f:
            writer = IncrementalPdfFileWriter(f, strict=False)
            with open(output_path, "wb") as out:
                try:
                    pdf_signer.sign_pdf(
                        writer,
                        appearance_text_params=appearance_params,
                        output=out,
                    )
                except Exception as e:
                    if "already" in str(e).lower() and "filled" in str(e).lower():
                        # Defensivo: con nombres de campo únicos no debería ocurrir.
                        raise RuntimeError(
                            f"El campo de firma '{field_name}' ya está ocupado en el PDF."
                        ) from e
                    raise
