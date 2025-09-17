[Setup]
AppName=MailSift Email Extractor
AppVersion=1.0
DefaultDirName={pf}\MailSift
DefaultGroupName=MailSift
OutputBaseFilename=MailSiftInstaller
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayIcon={app}\mail_icon.ico
SetupIconFile=mail_icon.ico

[Files]
; Main app executable
Source: "dist\gui.exe"; DestDir: "{app}"; Flags: ignoreversion

; HTML templates
Source: "templates\index.html"; DestDir: "{app}\templates"; Flags: ignoreversion
Source: "templates\paywall.html"; DestDir: "{app}\templates"; Flags: ignoreversion

; Static folder if used (optional)
Source: "static\*"; DestDir: "{app}\static"; Flags: ignoreversion recursesubdirs createallsubdirs

; App dependencies
Source: "app.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "gui.py"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\MailSift"; Filename: "{app}\gui.exe"
Name: "{userdesktop}\MailSift"; Filename: "{app}\gui.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: checkedonce

[Run]
Filename: "{app}\gui.exe"; Description: "Launch MailSift"; Flags: nowait postinstall skipifsilent
