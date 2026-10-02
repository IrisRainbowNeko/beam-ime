# SPDX-License-Identifier: Apache-2.0
param(
    [ValidateSet('Install','Uninstall','SystemInstall','SystemRestore','Doctor')][string]$Action = 'Install',
    [string]$InstallDir = $PSScriptRoot,
    [switch]$Download,
    [switch]$Cpu
)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Beam.Files.psm1') -Force
$Utf8 = New-Object Text.UTF8Encoding($false)
$State = Join-Path $env:LOCALAPPDATA 'beam-ime'
$RunKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'

function Find-Weasel {
    foreach ($key in 'HKLM:\SOFTWARE\Rime\Weasel','HKLM:\SOFTWARE\WOW6432Node\Rime\Weasel') {
        $entry = Get-ItemProperty $key -ErrorAction SilentlyContinue
        if ($entry -and $entry.WeaselRoot -and (Test-Path (Join-Path $entry.WeaselRoot 'WeaselServer.exe'))) { return $entry.WeaselRoot }
    }
    return $null
}

function Invoke-Process($File, $Arguments, [bool]$Elevated = $false, [int]$Timeout = 600000) {
    $start = New-Object Diagnostics.ProcessStartInfo $File
    $start.Arguments = $Arguments
    $start.UseShellExecute = $true
    $start.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    if ($Elevated) { $start.Verb = 'runas' }
    $process = [Diagnostics.Process]::Start($start)
    if ($Timeout -eq 0) { return }
    if (-not $process.WaitForExit($Timeout)) { $process.Kill(); throw "Timed out: $File" }
    if ($process.ExitCode -notin 0,3010) { throw "Process failed ($($process.ExitCode)): $File" }
}

function Stop-Weasel($Root) {
    if (Get-Process WeaselServer -ErrorAction SilentlyContinue) {
        try { Invoke-Process (Join-Path $Root 'WeaselServer.exe') '/q' $false 5000 } catch { Write-Warning $_ }
    }
    Get-Process WeaselServer,WeaselDeployer -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
}

function Check-Weasel($Root) {
    $exe = Join-Path $Root 'WeaselServer.exe'
    $version = (Get-Item $exe).VersionInfo.FileVersion
    if ($version -notmatch '^0\.17\.4(\.0)?\s*$') { throw "Beam requires Weasel 0.17.4.0; found $version. Existing installation was kept." }
    $bytes = [IO.File]::ReadAllBytes($exe)
    $pe = [BitConverter]::ToInt32($bytes,0x3c)
    if ([BitConverter]::ToUInt16($bytes,$pe+4) -ne 0x8664) { throw 'The 64-bit build of Weasel is required.' }
}

function Invoke-SystemStep($Step) {
    $powershell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $script = Join-Path $PSScriptRoot 'beam-setup.ps1'
    Invoke-Process $powershell "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -Action $Step -InstallDir `"$InstallDir`"" $true
}

function Update-SchemaList($Path, [bool]$Enable) {
    $marker = '# Beam IME'
    if (Test-Path $Path) { $text = [IO.File]::ReadAllText($Path) } else { $text = "patch:`r`n" }
    if ($Enable) {
        if ($text -match '(?m)^.*schema:\s*beam_ice') { return }
        $backup = "$Path.beam-backup"
        if ((Test-Path $Path) -and -not (Test-Path $backup)) { Copy-Item -LiteralPath $Path -Destination $backup }
        $lines = [Collections.Generic.List[string]]($text -split '\r?\n')
        $list = -1; $patch = -1
        for ($i=0; $i -lt $lines.Count; $i++) {
            if ($list -lt 0 -and $lines[$i] -match '^\s+schema_list\s*:\s*(#.*)?$') { $list=$i }
            if ($lines[$i] -match '^patch\s*:\s*(#.*)?$') { $patch=$i }
        }
        if ($list -ge 0) {
            $indent = ([regex]::Match($lines[$list], '^\s*').Value) + '  '
            for ($j=$list+1; $j -lt $lines.Count; $j++) {
                if ($lines[$j] -match '^(\s*)-') { $indent=$Matches[1]; break }
                if ($lines[$j] -match '^\s*[^\s#]') { break }
            }
            $lines.Insert($list+1, "$indent- schema: beam_ice  $marker")
        } elseif ($patch -ge 0) {
            if ($text -match 'schema_list/@before 0') { throw 'schema_list/@before 0 already exists; add beam_ice manually to avoid replacing it.' }
            $end = $patch+1
            while ($end -lt $lines.Count -and ($lines[$end] -match '^\s' -or $lines[$end] -match '^\s*(#.*)?$')) { $end++ }
            $lines.Insert($end, "  `"schema_list/@before 0`": { schema: beam_ice }  $marker")
        } else { throw 'Unsupported default.custom.yaml; add beam_ice to its schema list manually.' }
        $text = $lines -join "`r`n"
    } else {
        $text = (($text -split '\r?\n') | Where-Object { -not $_.EndsWith($marker) }) -join "`r`n"
    }
    [IO.File]::WriteAllText($Path, $text.TrimEnd()+"`r`n", $Utf8)
}

try {
    if ($Action -eq 'Doctor') {
        $endpoint = Join-Path $State 'beamd.endpoint'
        $status = @{version='0.1.0-beta.1'; weaselInstalled=[bool](Find-Weasel); running=[bool](Get-Process beamd -ErrorAction SilentlyContinue)}
        if (Test-Path $endpoint) {
            try {
                $info = Get-Content -Raw $endpoint | ConvertFrom-Json
                $client = New-Object Net.Sockets.TcpClient
                $client.ReceiveTimeout=3000; $client.SendTimeout=3000
                $client.Connect('127.0.0.1',[int]$info.port)
                $stream=$client.GetStream()
                $writer=New-Object IO.StreamWriter($stream,$Utf8); $writer.AutoFlush=$true
                $writer.WriteLine((@{id=1;op='health';token=$info.token} | ConvertTo-Json -Compress))
                $reader=New-Object IO.StreamReader($stream)
                $status.service=$reader.ReadLine() | ConvertFrom-Json
                $client.Close()
            } catch { $status.service=@{ok=$false;error='unavailable'} }
        }
        $status | ConvertTo-Json -Depth 5
        exit 0
    }
    $root=Find-Weasel
    if ($Action -in 'SystemInstall','SystemRestore') {
        if (-not $root) { throw 'Weasel is missing.' }
        Check-Weasel $root
        $systemState=Join-Path $root 'beam-backup'
        $hostRecord=Join-Path $systemState 'host.json'
        $hostHash=Get-FileHashValue (Join-Path $root 'WeaselServer.exe')
        if ((Test-Path $hostRecord) -and (Get-Content -Raw $hostRecord | ConvertFrom-Json).sha256 -ne $hostHash) {
            throw 'Weasel has changed since Beam was installed. Its current DLL and the backup were kept.'
        }
        Stop-Weasel $root
        if ($Action -eq 'SystemInstall') {
            Write-AtomicJson $hostRecord @{sha256=$hostHash;version='0.17.4.0'}
            $legacy=Join-Path $root 'rime.dll.orig'
            $fileRecord=Join-Path $systemState 'files.json'
            if ((Test-Path $legacy) -and -not (Test-Path $fileRecord)) {
                $backup=Join-Path $systemState 'legacy-original.dll'
                Copy-Item -LiteralPath $legacy -Destination $backup -Force
                Write-AtomicJson $fileRecord @(@{Path=(Join-Path $root 'rime.dll');Backup=$backup;Existed=$true;InstalledHash=(Get-FileHashValue (Join-Path $root 'rime.dll'))})
            }
            Install-ManagedFiles @(@{Source=(Join-Path $InstallDir 'payload\rime.dll');Destination=(Join-Path $root 'rime.dll')}) $systemState
        } else { Restore-ManagedFiles $systemState }
        exit 0
    }
    if ($Action -eq 'Install' -and -not $root) {
        $lock=Get-Content -Raw (Join-Path $InstallDir 'dependencies.lock.json') | ConvertFrom-Json
        $installer=Join-Path $InstallDir 'payload\weasel-installer.exe'
        if (-not (Test-Path $installer)) {
            if (-not $Download) { throw 'The offline Weasel installer is missing.' }
            Invoke-WebRequest -UseBasicParsing -Uri $lock.weasel.url -OutFile $installer
        }
        if ((Get-FileHashValue $installer) -ne $lock.weasel.sha256) { throw 'Weasel installer checksum mismatch.' }
        Invoke-Process $installer '/S' $true
        $root=Find-Weasel
    }
    if (-not $root) { throw 'Weasel was not found.' }
    Check-Weasel $root
    $entry=Get-ItemProperty 'HKCU:\Software\Rime\Weasel' -ErrorAction SilentlyContinue
    $userDir=if ($entry -and $entry.RimeUserDir) { [Environment]::ExpandEnvironmentVariables($entry.RimeUserDir) } else { Join-Path $env:APPDATA 'Rime' }
    [IO.Directory]::CreateDirectory($userDir) | Out-Null
    Get-Process beamd -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq (Join-Path $InstallDir 'beamd.exe') } | Stop-Process -Force -ErrorAction SilentlyContinue
    if ($Action -eq 'Uninstall') {
        Invoke-SystemStep 'SystemRestore'
        Remove-ItemProperty $RunKey -Name BeamIME -ErrorAction SilentlyContinue
        Update-SchemaList (Join-Path $userDir 'default.custom.yaml') $false
        Restore-ManagedFiles (Join-Path $State 'files')
    } else {
        $manifest=Get-Content -Raw (Join-Path $InstallDir 'models\default.json') | ConvertFrom-Json
        $model=Join-Path $State ("models\"+$manifest.filename)
        $source=Join-Path $InstallDir ('models\'+$manifest.filename)
        if (-not (Test-Path $source)) { $source=$null }
        Install-ModelFile $manifest $model $source ([bool]$Download)
        Stop-Weasel $root
        Invoke-SystemStep 'SystemInstall'
        try {
            $data=Join-Path $InstallDir 'payload\rime-data'
            $pairs=@(Get-ChildItem $data -Recurse -File | ForEach-Object {
                @{Source=$_.FullName;Destination=(Join-Path $userDir $_.FullName.Substring($data.Length+1))}
            })
            Install-ManagedFiles $pairs (Join-Path $State 'files')
            Update-SchemaList (Join-Path $userDir 'default.custom.yaml') $true
            $args="--supervise --model `"$model`" --log `"$State\beamd.log`""
            if ($Cpu) { $args += ' --ngl 0' }
            New-Item $RunKey -Force | Out-Null
            New-ItemProperty $RunKey -Name BeamIME -Value "`"$InstallDir\beamd.exe`" $args" -PropertyType String -Force | Out-Null
            Invoke-Process (Join-Path $InstallDir 'beamd.exe') $args $false 0
        } catch {
            Restore-ManagedFiles (Join-Path $State 'files')
            Invoke-SystemStep 'SystemRestore'
            throw
        }
    }
    Invoke-Process (Join-Path $root 'WeaselDeployer.exe') '/deploy'
    Invoke-Process (Join-Path $root 'WeaselServer.exe') '' $false 0
    Write-Host "Beam $Action completed. User dictionaries and models are retained on uninstall."
    exit 0
} catch { Write-Error $_ -ErrorAction Continue; exit 1 }
