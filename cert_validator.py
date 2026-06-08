import logging
from datetime import datetime, timezone
from typing import Optional

import requests
from asn1crypto import x509, pem
from cryptography import x509 as cx509
from cryptography.x509 import ocsp as cx509_ocsp
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.backends import default_backend
from pkcs11.constants import Attribute, ObjectClass

import config

log = logging.getLogger(__name__)


def extract_cert_from_token(session, key_id: bytes) -> x509.Certificate:
    results = list(session.get_objects({
        Attribute.CLASS: ObjectClass.CERTIFICATE,
        Attribute.ID: key_id,
    }))
    if not results:
        raise RuntimeError(
            "No se encontró ningún certificado en el token con el ID "
            f"'{key_id.hex()}'."
        )
    cert_der = bytes(results[0][Attribute.VALUE])
    return x509.Certificate.load(cert_der)


def _is_signing_cert(cert: x509.Certificate) -> bool:
    """True si el cert sirve para firmar (digital_signature / non_repudiation).

    Si el certificado no declara la extensión KeyUsage, se acepta (soft).
    """
    try:
        key_usage = cert.key_usage_value
    except Exception:
        return True
    if key_usage is None:
        return True
    usages = set(key_usage.native)
    return bool(usages & {'digital_signature', 'non_repudiation'})


def detect_signing_key_id(session) -> bytes:
    """Auto-detecta el CKA_ID del certificado de firma vigente del token.

    Enumera los certificados, descarta los expirados, prefiere los aptos para
    firma y confirma que exista la clave privada correspondiente. Si hay varios
    candidatos válidos elige el de vencimiento más lejano. Permite usar el mismo
    agente en distintas PCs sin configurar PRIVATE_KEY_ID en .env.
    """
    now = datetime.now(tz=timezone.utc)

    # PKCS#11 solo admite UNA búsqueda activa por sesión: no se pueden anidar
    # get_objects. Por eso primero recolectamos los CKA_ID de las claves privadas
    # (consumiendo la búsqueda por completo) y luego iteramos los certificados
    # comprobando pertenencia al set, sin búsquedas anidadas.
    priv_ids = set()
    for obj in session.get_objects({Attribute.CLASS: ObjectClass.PRIVATE_KEY}):
        try:
            priv_ids.add(bytes(obj[Attribute.ID]))
        except Exception as e:
            log.warning(f"No se pudo leer el ID de una clave privada del token: {e}")
            continue

    candidates = []  # (es_apto_firma: bool, not_after, cka_id)
    for obj in session.get_objects({Attribute.CLASS: ObjectClass.CERTIFICATE}):
        try:
            cka_id = bytes(obj[Attribute.ID])
            cert = x509.Certificate.load(bytes(obj[Attribute.VALUE]))
        except Exception as e:
            log.warning(f"No se pudo leer un certificado del token: {e}")
            continue

        # Debe existir la clave privada con el mismo CKA_ID para poder firmar.
        if not cka_id or cka_id not in priv_ids:
            if cka_id:
                log.info(
                    f"Certificado {cka_id.hex()} sin clave privada asociada; se omite."
                )
            continue

        validity = cert['tbs_certificate']['validity']
        not_after = validity['not_after'].chosen.native
        not_before = validity['not_before'].chosen.native
        if now < not_before or now > not_after:
            log.info(
                f"Certificado {cka_id.hex()} fuera de vigencia "
                f"({not_before:%d/%m/%Y}–{not_after:%d/%m/%Y}); se omite."
            )
            continue

        candidates.append((_is_signing_cert(cert), not_after, cka_id))

    if not candidates:
        raise RuntimeError(
            "No se encontró un certificado de firma vigente en el token. "
            "Verifique que el token esté conectado y que el certificado no haya expirado."
        )

    # Preferir aptos para firma, luego el de vencimiento más lejano.
    best = max(candidates, key=lambda c: (c[0], c[1]))
    cka_id = best[2]
    log.info(f"CKA_ID de firma auto-detectado: {cka_id.hex()}")
    return cka_id


def get_cn_from_cert(cert: x509.Certificate) -> str:
    for rdn in cert.subject.chosen:
        for atv in rdn:
            if atv['type'].native == 'common_name':
                return atv['value'].native
    return "Firmante Desconocido"


def get_email_from_cert(cert: x509.Certificate) -> Optional[str]:
    """Devuelve el email del firmante: primero del SAN (rfc822_name),
    como respaldo del emailAddress del subject. None si no hay ninguno."""
    san = cert.subject_alt_name_value
    if san is not None:
        for gn in san:
            if gn.name == 'rfc822_name':
                return gn.chosen.native
    for rdn in cert.subject.chosen:
        for atv in rdn:
            if atv['type'].native == 'email_address':
                return atv['value'].native
    return None


def check_expiry(cert: x509.Certificate) -> None:
    now = datetime.now(tz=timezone.utc)
    not_after = cert['tbs_certificate']['validity']['not_after'].chosen.native
    if now > not_after:
        raise ValueError(
            f"El certificado del token ha expirado el "
            f"{not_after.strftime('%d/%m/%Y %H:%M:%S')} UTC."
        )


def check_san_email(cert: x509.Certificate, signer_email: str) -> None:
    san = cert.subject_alt_name_value
    if san is None:
        raise ValueError(
            "El certificado no contiene la extensión SubjectAlternativeName. "
            "No se puede verificar la identidad del firmante."
        )
    emails_in_cert = [gn.chosen.native for gn in san if gn.name == 'rfc822_name']
    if not any(e.lower() == signer_email.lower() for e in emails_in_cert):
        raise ValueError(
            f"El email del firmante '{signer_email}' no coincide con ninguno "
            f"de los emails en el certificado: {emails_in_cert}. "
            "Verifique que está usando el certificado correcto."
        )


def _fetch_issuer_cert(cert: x509.Certificate) -> Optional[x509.Certificate]:
    aia = cert.authority_information_access_value
    if aia is None:
        return None

    ca_issuer_url = None
    for entry in aia:
        if entry['access_method'].native == 'ca_issuers':
            location = entry['access_location']
            if location.name == 'uniform_resource_identifier':
                ca_issuer_url = location.chosen.native
                break

    if ca_issuer_url is None:
        return None

    try:
        resp = requests.get(ca_issuer_url, timeout=10)
        resp.raise_for_status()
        data = resp.content
        if pem.detect(data):
            _, _, data = pem.unarmor(data)
        return x509.Certificate.load(data)
    except Exception as e:
        log.warning(f"No se pudo obtener el certificado del emisor desde '{ca_issuer_url}': {e}")
        return None


def _check_ocsp(cert: x509.Certificate, issuer_cert: x509.Certificate) -> bool:
    ocsp_urls = cert.ocsp_urls
    if not ocsp_urls:
        return False

    c_cert = cx509.load_der_x509_certificate(cert.dump(), default_backend())
    c_issuer = cx509.load_der_x509_certificate(issuer_cert.dump(), default_backend())

    builder = cx509_ocsp.OCSPRequestBuilder()
    builder = builder.add_certificate(c_cert, c_issuer, hashes.SHA1())
    ocsp_request = builder.build()
    ocsp_request_data = ocsp_request.public_bytes(serialization.Encoding.DER)

    for url in ocsp_urls:
        try:
            resp = requests.post(
                url,
                data=ocsp_request_data,
                headers={'Content-Type': 'application/ocsp-request'},
                timeout=10,
            )
            resp.raise_for_status()
            ocsp_response = cx509_ocsp.load_der_ocsp_response(resp.content)
            if ocsp_response.response_status == cx509_ocsp.OCSPResponseStatus.SUCCESSFUL:
                if ocsp_response.certificate_status == cx509_ocsp.OCSPCertStatus.REVOKED:
                    raise ValueError(
                        "El certificado del token ha sido REVOCADO según el "
                        f"respondedor OCSP ({url}). La firma no puede procesarse."
                    )
                return True
        except ValueError:
            raise
        except Exception as e:
            log.warning(f"Error al consultar OCSP en '{url}': {e}")
            continue

    return False


def _check_crl(cert: x509.Certificate) -> bool:
    dp_list = cert.crl_distribution_points
    if not dp_list:
        return False

    serial = cert.serial_number

    for dp in dp_list:
        dp_name = dp['distribution_point']
        if dp_name.name != 'full_name':
            continue
        for gn in dp_name.chosen:
            if gn.name != 'uniform_resource_identifier':
                continue
            url = gn.chosen.native
            try:
                resp = requests.get(url, timeout=10)
                resp.raise_for_status()
                data = resp.content
                if pem.detect(data):
                    _, _, data = pem.unarmor(data)
                crl_obj = cx509.load_der_x509_crl(data, default_backend())
                revoked = crl_obj.get_revoked_certificate_by_serial_number(serial)
                if revoked is not None:
                    raise ValueError(
                        "El certificado del token se encuentra en la Lista de "
                        f"Revocación de Certificados (CRL: {url}). "
                        "La firma no puede procesarse."
                    )
                return True
            except ValueError:
                raise
            except Exception as e:
                log.warning(f"Error al consultar CRL en '{url}': {e}")
                continue

    return False


def validate_certificate(session, key_id: bytes, signer_email: Optional[str] = None) -> x509.Certificate:
    """Valida el certificado del token antes de firmar.

    Verifica vigencia, email SAN (si se provee) y estado de revocación (soft-fail).
    Retorna el certificado parseado para uso posterior (ej: extraer CN).
    """
    cert = extract_cert_from_token(session, key_id)
    check_expiry(cert)

    if signer_email is not None:
        check_san_email(cert, signer_email)

    issuer_cert = _fetch_issuer_cert(cert)
    if issuer_cert is None:
        log.warning(
            "No se pudo obtener el certificado del emisor; "
            "se omite la verificación de revocación."
        )
        return cert

    try:
        ocsp_ok = _check_ocsp(cert, issuer_cert)
    except ValueError:
        raise
    except Exception as e:
        log.warning(f"Verificación OCSP falló inesperadamente: {e}")
        ocsp_ok = False

    if not ocsp_ok:
        try:
            crl_ok = _check_crl(cert)
            if not crl_ok:
                log.warning(
                    "No se pudo verificar el estado de revocación por OCSP ni CRL. "
                    "Se continúa con la firma (soft-fail)."
                )
        except ValueError:
            raise
        except Exception as e:
            log.warning(
                f"Verificación CRL también falló ({e}). "
                "Se continúa sin verificación de revocación."
            )

    return cert
