# SPDX-License-Identifier: Apache-2.0
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-AtomicJson($Path, $Value) {
    $parent = Split-Path $Path
    [IO.Directory]::CreateDirectory($parent) | Out-Null
    $temp = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    [IO.File]::WriteAllText($temp, ($Value | ConvertTo-Json -Depth 20), (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temp -Destination $Path -Force
}

function Get-FileHashValue($Path) {
    if (Test-Path -LiteralPath $Path -PathType Leaf) { return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
    return $null
}

function Install-ManagedFiles($Pairs, $StateDir) {
    [IO.Directory]::CreateDirectory($StateDir) | Out-Null
    $manifest = Join-Path $StateDir 'files.json'
    $records = @()
    $previous = if (Test-Path $manifest) { [IO.File]::ReadAllBytes($manifest) } else { $null }
    if (Test-Path $manifest) { $records = @((Get-Content -Raw $manifest | ConvertFrom-Json)) }
    $attempt = @()
    try {
        foreach ($pair in $Pairs) {
            $dest = [IO.Path]::GetFullPath($pair.Destination)
            $prior = @($records | Where-Object { $_.Path -eq $dest })
            $currentHash = Get-FileHashValue $dest
            if ($prior.Count -and $currentHash -and $currentHash -ne $prior[0].InstalledHash) {
                Write-Warning "Keeping modified file: $dest"
                continue
            }
            $backup = Join-Path $StateDir ([guid]::NewGuid().ToString('N') + '.bak')
            if ($currentHash) { Copy-Item -LiteralPath $dest -Destination $backup }
            $attempt += [pscustomobject]@{Path=$dest; Backup=$backup; Existed=[bool]$currentHash}
            $record = if ($prior.Count) { $prior[0] } else {
                [pscustomobject]@{Path=$dest; Backup=$backup; Existed=[bool]$currentHash; InstalledHash=''}
            }
            # Persist restoration information before touching the destination.
            $record.InstalledHash = Get-FileHashValue $pair.Source
            if (-not $record.InstalledHash) { throw "Missing package file: $($pair.Source)" }
            $records = @($records | Where-Object { $_.Path -ne $dest }) + @($record)
            Write-AtomicJson $manifest $records
            [IO.Directory]::CreateDirectory((Split-Path $dest)) | Out-Null
            Copy-Item -LiteralPath $pair.Source -Destination $dest -Force
        }
    } catch {
        [array]::Reverse($attempt)
        foreach ($entry in $attempt) {
            if ($entry.Existed) { Copy-Item -LiteralPath $entry.Backup -Destination $entry.Path -Force }
            else { Remove-Item -LiteralPath $entry.Path -Force -ErrorAction SilentlyContinue }
        }
        if ($null -ne $previous) { [IO.File]::WriteAllBytes($manifest, $previous) }
        else { Remove-Item -LiteralPath $manifest -Force -ErrorAction SilentlyContinue }
        throw
    }
}

function Restore-ManagedFiles($StateDir) {
    $manifest = Join-Path $StateDir 'files.json'
    if (-not (Test-Path $manifest)) { return }
    $records = @((Get-Content -Raw $manifest | ConvertFrom-Json))
    foreach ($entry in $records) {
        $hash = Get-FileHashValue $entry.Path
        if ($hash -and $hash -ne $entry.InstalledHash) { Write-Warning "Keeping modified file: $($entry.Path)"; continue }
        if ($entry.Existed) {
            if (-not (Test-Path -LiteralPath $entry.Backup)) { throw "Missing backup: $($entry.Backup)" }
            Copy-Item -LiteralPath $entry.Backup -Destination $entry.Path -Force
        } elseif ($hash) { Remove-Item -LiteralPath $entry.Path -Force }
    }
    # Keep backups so a later manual repair can still recover them.
    Move-Item -LiteralPath $manifest -Destination (Join-Path $StateDir 'restored-files.json') -Force
}

function Install-ModelFile($Manifest, $Target, $Source, [bool]$Download) {
    if ((Get-FileHashValue $Target) -eq $Manifest.sha256) { return }
    [IO.Directory]::CreateDirectory((Split-Path $Target)) | Out-Null
    $temporary = "$Target.part"
    try {
        if ($Source) { Copy-Item -LiteralPath $Source -Destination $temporary -Force }
        elseif ($Download) {
            if (-not $Manifest.url.StartsWith('https://')) { throw 'Model URL must use HTTPS' }
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -UseBasicParsing -Uri $Manifest.url -OutFile $temporary
        } else { throw 'The offline model is missing.' }
        if ((Get-Item $temporary).Length -ne $Manifest.size -or (Get-FileHashValue $temporary) -ne $Manifest.sha256) {
            throw 'Model size or SHA-256 mismatch; the previous model has been kept.'
        }
        Move-Item -LiteralPath $temporary -Destination $Target -Force
    } finally { Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue }
}
Export-ModuleMember -Function Write-AtomicJson,Get-FileHashValue,Install-ManagedFiles,Restore-ManagedFiles,Install-ModelFile
