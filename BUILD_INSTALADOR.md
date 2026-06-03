# Build y distribución del instalador del Agente de Firma

Cómo generar un único `Instalador_AgenteFirma.exe` para desplegar el agente
`firmador://` en las PCs de los usuarios **sin Python y sin permisos de
Administrador**. Se arma **una vez** en una PC de desarrollo; los usuarios solo
ejecutan el instalador resultante.

## Resumen del flujo

```
agent.py + módulos  --PyInstaller-->  dist\AgenteFirma.exe  --Inno Setup-->  Instalador_AgenteFirma.exe
```

> **⚠️ Qué se distribuye a los usuarios:** `Output\Instalador_AgenteFirma.exe`,
> **NO** `dist\AgenteFirma.exe`. El `.exe` "pelado" es solo el agente (la ventana
> de PIN) y **no registra el protocolo `firmador://` en el registro**: si lo copiás
> suelto a otra PC, no aparece nada en regedit. Quien crea las claves en
> `HKCU\Software\Classes\firmador` es el instalador de Inno Setup. No hace falta
> Administrador (el instalador usa `PrivilegesRequired=lowest` / HKCU).

- `AgenteFirma.spec` → receta de PyInstaller (genera el .exe standalone).
- `instalador.iss` → script de Inno Setup (instala per-user + registra el protocolo en HKCU).
- `install_protocol_user.py` → registro manual del protocolo sin admin (para pruebas; en producción lo hace el .iss).

## Prerrequisito en CADA PC de usuario (no lo cubre el instalador)

**SafeNet Authentication Client** debe estar instalado: provee
`C:\Windows\System32\eTPKCS11.dll`. Sin ese driver el agente falla al iniciar
(`config.py` lanza `FileNotFoundError`). Es el middleware del fabricante del
token y se instala por separado. El token USB debe estar conectado al firmar.

---

## Paso 1 — Generar el ejecutable con PyInstaller (PC de desarrollo)

```powershell
venv\Scripts\python.exe -m pip install pyinstaller
venv\Scripts\pyinstaller --noconfirm AgenteFirma.spec
```

Resultado: `dist\AgenteFirma.exe`.

> El `.spec` recolecta `pyhanko`, `pyhanko_certvalidator`, `pkcs11`,
> `asn1crypto`, `cryptography` y `oscrypto` con `collect_all`, porque cargan
> datos/submódulos dinámicamente. Si al ejecutar el .exe aparece un
> `ModuleNotFoundError`, agregar el módulo faltante a `hiddenimports` en
> `AgenteFirma.spec` y recompilar.

### Probar el .exe antes de empaquetar el instalador
Con el token conectado y SafeNet instalado:

```powershell
dist\AgenteFirma.exe "firmador://firmar?token=test&pdf_url=https://servidor/pdf&upload_url=https://servidor/upload"
```

Debe abrir la ventana de PIN. (Sin argumentos muestra el aviso "se invoca por el navegador".)

---

## Paso 2 — Compilar el instalador con Inno Setup

1. Instalar **Inno Setup** (https://jrsoftware.org/isdl.php).
2. Abrir `instalador.iss` en el Inno Setup Compiler y presionar **Compile**
   (o por consola: `ISCC.exe instalador.iss`).

Resultado: `Output\Instalador_AgenteFirma.exe` — esto es lo que se distribuye.

`instalador.iss` está configurado para:
- `PrivilegesRequired=lowest` → instala en `%APPDATA%\AgenteFirma` sin admin.
- Registrar `firmador://` en `HKCU\Software\Classes\firmador` apuntando a
  `"{app}\AgenteFirma.exe" "%1"`.
- Acceso directo en el Menú Inicio y desinstalador (limpia archivos y claves HKCU).

---

## Paso 3 — Instalación en la PC del usuario

1. (Una vez) Instalar **SafeNet Authentication Client**.
2. Ejecutar `Instalador_AgenteFirma.exe` → Siguiente → Finalizar.
3. Abrir `firmador://...` desde el sistema web → se lanza el agente, pide PIN, firma y sube.

---

## Consideraciones importantes con `--onefile`

Con un build **onefile**, al ejecutarse el .exe se descomprime en una carpeta
temporal y `Path(__file__).parent` (usado en `agent.py` y `config.py`) apunta a
ese temporal, que se borra al cerrar. Consecuencias:

- **`agent.log`** se escribe en el temporal (se pierde al salir). Si se necesita
  log persistente para soporte, conviene usar build **onedir** (cambiar el `EXE`
  del `.spec` por `COLLECT`) o ajustar `agent.py` para escribir el log en
  `%APPDATA%\AgenteFirma`.
- **Copia local del firmado** (`FIRMADOS_PATH`, default `./pdfs_firmados`) también
  caería en el temporal. La firma y la **subida al servidor siguen funcionando**;
  solo se pierde la copia local. Para conservarla, definir `FIRMADOS_PATH` con una
  ruta **absoluta** en un `.env` empaquetado, p.ej.:

  ```
  FIRMADOS_PATH=%USERPROFILE%\Documents\Firmados
  ```

  (y descomentar la línea de `.env` en la sección `[Files]` de `instalador.iss`).

Si estas pérdidas no importan (la subida al servidor es la fuente de verdad),
el build onefile tal cual es suficiente.

---

## Alternativa sin instalador (registro manual)

Si en una PC puntual ya está el `AgenteFirma.exe` copiado y solo falta registrar
el protocolo (sin admin):

```powershell
python install_protocol_user.py --exe "C:\ruta\AgenteFirma.exe"
# quitar el registro:
python install_protocol_user.py --remove
```

> `install_protocol.py` (el original, HKEY_CLASSES_ROOT) sigue sirviendo solo
> para el flujo con Python instalado y **requiere Administrador**; no usarlo en
> PCs de usuarios sin admin.

---

## Verificación end-to-end (PC limpia, idealmente cuenta sin admin)

1. Instalar SafeNet + ejecutar `Instalador_AgenteFirma.exe`.
2. Confirmar el registro: `HKCU\Software\Classes\firmador\shell\open\command`
   = `"...\AppData\Roaming\AgenteFirma\AgenteFirma.exe" "%1"`.
3. Con token conectado, abrir `firmador://firmar?token=...&pdf_url=...&upload_url=...`.
4. Verificar ventana de PIN → firma → subida OK.
5. Probar token desconectado y PIN incorrecto → mensajes en español correctos.
6. Probar la desinstalación: borra archivos y claves HKCU.
