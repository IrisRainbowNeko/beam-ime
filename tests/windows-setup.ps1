param([string]$WeaselExe)
$ErrorActionPreference='Stop'
$temporary=Join-Path ([IO.Path]::GetTempPath()) ('beam-setup-test-'+[guid]::NewGuid().ToString('N'))
$localAppData=$env:LOCALAPPDATA
$appData=$env:APPDATA
[IO.Directory]::CreateDirectory($temporary) | Out-Null
try {
    $env:LOCALAPPDATA=$temporary
    $env:APPDATA=$temporary
    . (Join-Path $PSScriptRoot '../packaging/windows/beam-setup.ps1') -InstallDir $temporary
    if ($WeaselExe) {
        Check-Weasel (Split-Path $WeaselExe)
        Write-Host 'Official Weasel executable accepted.'
    }

    # Fresh Weasel installations create an empty default.custom.yaml.
    $schema=Join-Path $temporary 'default.custom.yaml'
    foreach ($empty in @(''," `r`n")) {
        [IO.File]::WriteAllText($schema,$empty)
        Update-SchemaList $schema $true
        Update-SchemaList $schema $true
        $text=[IO.File]::ReadAllText($schema)
        if ($text -notmatch '^patch:' -or ([regex]::Matches($text,'schema: beam_ice')).Count -ne 1) {
            throw 'empty Weasel configuration was not initialized idempotently'
        }
        Update-SchemaList $schema $false
        if ([IO.File]::ReadAllText($schema) -match 'beam_ice') { throw 'Beam schema was not removed' }
    }

    $script:weaselRoot=Join-Path $temporary 'Weasel [host]'
    [IO.Directory]::CreateDirectory($script:weaselRoot) | Out-Null
    $exe=Join-Path $script:weaselRoot 'WeaselServer.exe'
    $bytes=New-Object byte[] 256
    $bytes[0x3c]=0x80; $bytes[0x84]=0x64; $bytes[0x85]=0x86
    [IO.File]::WriteAllBytes($exe,$bytes)
    $script:versionInfo=[pscustomobject]@{FileVersion='';FileMajorPart=0;FileMinorPart=17;FileBuildPart=4;FilePrivatePart=0}
    function Get-Item { param($LiteralPath) [pscustomobject]@{VersionInfo=$script:versionInfo} }
    Check-Weasel $script:weaselRoot
    $script:versionInfo.FileBuildPart=3
    try { Check-Weasel $script:weaselRoot; throw 'accepted wrong version' }
    catch { if ($_.Exception.Message -notlike '*found 0.17.3.0*') { throw } }
    $script:versionInfo.FileBuildPart=4
    $bytes[0x84]=0x4c; $bytes[0x85]=0x01
    [IO.File]::WriteAllBytes($exe,$bytes)
    try { Check-Weasel $script:weaselRoot; throw 'accepted x86 host' }
    catch { if ($_.Exception.Message -ne 'The 64-bit build of Weasel is required.') { throw } }
    Remove-Item Function:Get-Item

    $script:userDir=Join-Path $temporary 'Rime'
    $script:calls=New-Object 'Collections.Generic.List[string]'
    function Find-Weasel { $script:weaselRoot }
    function Check-Weasel { throw 'Uninstall must not re-run installation version checks.' }
    function Get-ItemProperty { [pscustomobject]@{RimeUserDir=$script:userDir} }
    function Get-Process { }
    function Remove-ItemProperty { $script:calls.Add('remove-startup') }
    function Stop-Weasel { $script:calls.Add('stop-host') }
    function Invoke-Process { param($File,$Arguments) $script:calls.Add((Split-Path $File -Leaf)) }
    function Invoke-SystemStep { param($Step) throw "Unexpected elevated step: $Step" }

    # A failed version check has no system or user transaction to restore.
    $Action='Uninstall'
    Invoke-Setup
    if ($script:calls.Count -ne 1 -or $script:calls[0] -ne 'remove-startup') { throw 'failed install touched the host' }
    if (Test-Path -LiteralPath $script:userDir) { throw 'failed uninstall created Rime configuration' }
    [IO.Directory]::CreateDirectory($script:userDir) | Out-Null
    $config=Join-Path $script:userDir 'default.custom.yaml'
    $original="patch:`n  menu/page_size: 8`n"
    [IO.File]::WriteAllText($config,$original)
    Invoke-Setup
    if ([IO.File]::ReadAllText($config) -cne $original) { throw 'unowned config changed' }
    $savedRoot=$script:weaselRoot
    $script:weaselRoot=$null
    Invoke-Setup
    $script:weaselRoot=$savedRoot

    $Action='SystemRestore'
    $script:calls.Clear()
    Invoke-Setup
    if ($script:calls.Count) { throw 'empty system recovery stopped the host' }

    # An actual DLL transaction restores by recorded identity, not version text.
    $dll=Join-Path $script:weaselRoot 'rime.dll'
    $replacement=Join-Path $temporary 'rime.dll'
    $systemState=Join-Path $script:weaselRoot 'beam-backup'
    [IO.File]::WriteAllText($dll,'original DLL')
    [IO.File]::WriteAllText($replacement,'Beam DLL')
    Write-AtomicJson (Join-Path $systemState 'host.json') @{sha256=(Get-FileHashValue $exe)}
    Install-ManagedFiles @(@{Source=$replacement;Destination=$dll}) $systemState
    Invoke-Setup
    if ([IO.File]::ReadAllText($dll) -ne 'original DLL') { throw 'original DLL not restored' }
    Install-ManagedFiles @(@{Source=$replacement;Destination=$dll}) $systemState
    [IO.File]::WriteAllText($exe,'upgraded host')
    try { Invoke-Setup; throw 'restored into changed host' }
    catch { if ($_.Exception.Message -notlike 'Weasel has changed*') { throw } }
    if ([IO.File]::ReadAllText($dll) -ne 'Beam DLL') { throw 'changed host DLL was overwritten' }
    if (-not (Test-Path -LiteralPath (Join-Path $systemState 'files.json'))) { throw 'recovery state lost' }
    Write-Host 'Windows setup regression tests passed.'
} finally {
    $env:LOCALAPPDATA=$localAppData
    $env:APPDATA=$appData
    Remove-Item -LiteralPath $temporary -Recurse -Force
}
