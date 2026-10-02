Unicode true
!include MUI2.nsh
!include x64.nsh
!include LogicLib.nsh
Name "Beam ${VERSION}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\BeamIME"
RequestExecutionLevel user
SetCompressor lzma
ShowInstDetails show
ShowUninstDetails show
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\BeamIME"
!define POWERSHELL "$WINDIR\sysnative\WindowsPowerShell\v1.0\powershell.exe"
!define MUI_WELCOMEPAGE_TEXT "安装 Beam 本地大模型输入法。轻量版会下载约 610 MiB 模型和必要的小狼毫组件；离线版已包含它们。安装小狼毫或更新其核心时会请求管理员权限。"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"
Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "需要 64 位 Windows。"
    Abort
  ${EndIf}
FunctionEnd
Section
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  File /oname=beam-stop.ps1 "${STAGE}\beam-stop.ps1"
  nsExec::ExecToLog '"${POWERSHELL}" -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\beam-stop.ps1" -InstallDir "$INSTDIR"'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "无法停止当前 Beam 服务，程序文件尚未覆盖。"
    Abort
  ${EndIf}
  SetOutPath "$INSTDIR"
  File /r /x models "${STAGE}\*"
  SetOutPath "$INSTDIR\models"
  File "${STAGE}\models\default.json"
!ifdef OFFLINE
  SetCompress off
  File "${STAGE}\models\beam-0.6b-q8_0.gguf"
  SetCompress auto
!endif
  WriteUninstaller "$INSTDIR\uninstall.exe"
  ; Register recovery before setup: a failed deployment must remain uninstallable.
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "Beam 输入法"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  nsExec::ExecToLog '"${POWERSHELL}" -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\beam-setup.ps1" -Action Install -InstallDir "$INSTDIR" ${SETUP_FLAGS}'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "安装没有完成。详情见日志；可在应用列表运行卸载恢复。"
    Abort
  ${EndIf}
SectionEnd
Section "Uninstall"
  nsExec::ExecToLog '"${POWERSHELL}" -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\beam-setup.ps1" -Action Uninstall -InstallDir "$INSTDIR"'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "恢复未完成，备份和卸载程序已保留。"
    Abort
  ${EndIf}
  DeleteRegKey HKCU "${UNINST_KEY}"
  Delete "$INSTDIR\*.exe"
  Delete "$INSTDIR\*.dll"
  Delete "$INSTDIR\*.ps1"
  Delete "$INSTDIR\*.psm1"
  Delete "$INSTDIR\dependencies.lock.json"
  RMDir /r "$INSTDIR\payload"
  RMDir /r "$INSTDIR\licenses"
  ; Downloaded models and backups live under LOCALAPPDATA\beam-ime and are retained.
  RMDir "$INSTDIR"
SectionEnd
