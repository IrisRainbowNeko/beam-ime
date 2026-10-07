$ErrorActionPreference='Stop'
Import-Module (Join-Path $PSScriptRoot '../packaging/windows/Beam.Files.psm1') -Force
$unicode=[string][char]0x8f93+[char]0x5165+[char]0x6cd5
$root=Join-Path ([IO.Path]::GetTempPath()) ("$unicode [Beam test] " + [guid]::NewGuid().ToString('N'))
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
    if (Test-Path -LiteralPath (Join-Path $freshState 'files.json')) { throw 'failed transaction left stale state' }
    $e=$null;$t=$null
    [void][Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot '../packaging/windows/beam-setup.ps1'),[ref]$t,[ref]$e)
    if ($e) { throw ($e | Out-String) }
    [void][Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot '../packaging/windows/beam-stop.ps1'),[ref]$t,[ref]$e)
    if ($e) { throw ($e | Out-String) }
    . (Join-Path $PSScriptRoot '../packaging/windows/beamctl.ps1')
    $state=Join-Path $root 'learning state'
    $Source=$root
    $Manifest=Join-Path $root 'learning.json'
    $archive=Join-Path $root 'runtime.zip'
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip=[IO.Compression.ZipFile]::Open($archive,[IO.Compression.ZipArchiveMode]::Create)
    $longName=('long_directory/'*18)+'data.txt'
    try {
        foreach ($name in @('component.json',$longName)) {
            $writer=New-Object IO.StreamWriter($zip.CreateEntry($name).Open())
            try { $writer.Write($(if ($name -eq 'component.json') {'{"schemaVersion":2,"base_sha256":"test","backend":"vulkan"}'} else {'long path'})) }
            finally { $writer.Dispose() }
        }
    } finally { $zip.Dispose() }
    @{schemaVersion=2; platform='windows-x86_64';backend='vulkan';base_sha256='test';assets=@(@{
        filename='runtime.zip';size=(Get-Item -LiteralPath $archive).Length;sha256=(Get-FileHash -LiteralPath $archive).Hash.ToLower()
    })} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Manifest -Encoding UTF8
    $installed=Install-LearningComponent
    if (-not (Test-Path -LiteralPath $installed)) { throw 'learning component was not installed' }
    Add-Type 'public static class BeamTestPath { [System.Runtime.InteropServices.DllImport("kernel32.dll",CharSet=System.Runtime.InteropServices.CharSet.Unicode)] public static extern uint GetFileAttributesW(string path); }'
    $longPath='\\?\'+(Join-Path (Split-Path $installed) ($longName.Replace('/','\')))
    if ([BeamTestPath]::GetFileAttributesW($longPath) -eq [uint32]::MaxValue) { throw 'long-path asset was not extracted' }
    if ((Install-LearningComponent) -ne $installed) { throw 'learning install is not idempotent' }
    Write-Host 'Windows file transaction tests passed.'
} finally { & cmd.exe /d /c "rmdir /s /q `"\\?\$root`"" }
