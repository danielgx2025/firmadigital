; ============================================================================
; Inno Setup - Instalador PER-USER del Agente de Firma (firmador://)
;
; Instala AgenteFirma.exe en %APPDATA%\AgenteFirma (sin permisos de
; Administrador) y registra el protocolo firmador:// en HKEY_CURRENT_USER.
;
; Requisitos previos en la PC del usuario (NO los instala este setup):
;   - SafeNet Authentication Client (provee C:\Windows\System32\eTPKCS11.dll)
;   - Token USB conectado al momento de firmar
;
; Antes de compilar:
;   1) Generar dist\AgenteFirma.exe con PyInstaller (ver BUILD_INSTALADOR.md)
;   2) Abrir este .iss con Inno Setup Compiler y presionar "Compile"
;      (o: ISCC.exe instalador.iss)
;
; Salida: Output\Instalador_AgenteFirma.exe
; ============================================================================

#define MyAppName "Agente de Firma Digital"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Poder Judicial de Salta"
#define MyAppExeName "AgenteFirma.exe"

[Setup]
AppId={{B53BD098-63D0-4281-9ECE-DF0A46542AED}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; Instalación per-user: sin elevación de Administrador
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={userappdata}\AgenteFirma
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputBaseFilename=Instalador_AgenteFirma
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; Cierra la app si está corriendo durante la (des)instalación
CloseApplications=yes

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Files]
; Ejecutable empaquetado por PyInstaller (--onefile)
Source: "dist\AgenteFirma.exe"; DestDir: "{app}"; Flags: ignoreversion
; OPCIONAL: incluir .env solo si se quiere fijar SELF_BASE_URL / *_PATH.
; Con auto-detección del certificado NO es imprescindible. Descomentar si se usa:
; Source: ".env"; DestDir: "{app}"; Flags: onlyifdoesntexist

[Registry]
; Protocolo firmador:// en HKEY_CURRENT_USER (equivalente per-user de HKCR).
; uninsdeletekey en la clave raíz borra todo el árbol al desinstalar.
Root: HKCU; Subkey: "Software\Classes\firmador"; ValueType: string; ValueName: ""; ValueData: "URL:firmador Protocol"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\firmador"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\firmador\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

[Icons]
; Acceso directo en el Menú Inicio (opcional). El agente normalmente se lanza
; solo por el protocolo, pero el acceso sirve para verificar que abre.
Name: "{userprograms}\{#MyAppName}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{userprograms}\{#MyAppName}\Desinstalar {#MyAppName}"; Filename: "{uninstallexe}"

[Run]
; Ofrecer abrir el agente al finalizar (mostrará el aviso de "se invoca por el navegador").
Filename: "{app}\{#MyAppExeName}"; Description: "Verificar el agente ahora"; Flags: nowait postinstall skipifsilent
