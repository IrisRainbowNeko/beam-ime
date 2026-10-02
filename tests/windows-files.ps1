$ErrorActionPreference='Stop'
Import-Module (Join-Path $PSScriptRoot '../packaging/windows/Beam.Files.psm1') -Force
$root=Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($root) | Out-Null
try {
    $source=Join-Path $root 'package'
    $target=Join-Path $root 'user-file'
    $state=Join-Path $root 'state'
    [IO.File]::WriteAllText($source,'new')
    [IO.File]::WriteAllText($target,'original')
    $pair=@{Source=$source;Destination=$target}
    Install-ManagedFiles @($pair) $state
    Install-ManagedFiles @($pair) $state
    Restore-ManagedFiles $state
    if ([IO.File]::ReadAllText($target) -ne 'original') { throw 'original file was not restored' }
    Install-ManagedFiles @($pair) $state
    [IO.File]::WriteAllText($target,'user change')
    Restore-ManagedFiles $state
    if ([IO.File]::ReadAllText($target) -ne 'user change') { throw 'user changes were lost' }
    $freshState=Join-Path $root 'rollback'
    try {
        Install-ManagedFiles @($pair,@{Source=(Join-Path $root 'missing');Destination=(Join-Path $root 'other')}) $freshState
        throw 'expected copy failure'
    } catch {
        if ($_.Exception.Message -eq 'expected copy failure') { throw }
    }
    if ([IO.File]::ReadAllText($target) -ne 'user change') { throw 'failed transaction did not roll back' }
    if (Test-Path (Join-Path $freshState 'files.json')) { throw 'failed transaction left stale state' }
    $e=$null;$t=$null
    [void][Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot '../packaging/windows/beam-setup.ps1'),[ref]$t,[ref]$e)
    if ($e) { throw ($e | Out-String) }
    [void][Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot '../packaging/windows/beam-stop.ps1'),[ref]$t,[ref]$e)
    if ($e) { throw ($e | Out-String) }
    Write-Host 'Windows file transaction tests passed.'
} finally { Remove-Item -LiteralPath $root -Recurse -Force }
