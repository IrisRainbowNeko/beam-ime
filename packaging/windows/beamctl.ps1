# SPDX-License-Identifier: Apache-2.0
param(
    [ValidateSet('learn')][string]$Command='learn',
    [ValidateSet('enable','disable','pause','resume','status','train','rollback','reset','install')][string]$Action='status',
    [string]$Manifest, [string]$Source, [switch]$Download, [switch]$Yes
)
$ErrorActionPreference='Stop'
$state=Join-Path $env:LOCALAPPDATA 'beam-ime'

function Invoke-BeamRequest($Request) {
    $endpoint=Get-Content -LiteralPath (Join-Path $state 'beamd.endpoint') -Raw -Encoding UTF8 | ConvertFrom-Json
    $Request.id=1; $Request.token=$endpoint.token
    $client=New-Object Net.Sockets.TcpClient
    try {
        $client.Connect('127.0.0.1',[int]$endpoint.port)
        $stream=$client.GetStream(); $stream.ReadTimeout=10000; $stream.WriteTimeout=10000
        $bytes=[Text.Encoding]::UTF8.GetBytes(($Request | ConvertTo-Json -Depth 12 -Compress)+"`n")
        $stream.Write($bytes,0,$bytes.Length)
        $reader=New-Object IO.StreamReader($stream,[Text.Encoding]::UTF8)
        $result=$reader.ReadLine() | ConvertFrom-Json
        if (-not $result.ok) { throw $result.error }
        return $result
    } finally { $client.Dispose() }
}

function Install-LearningComponent {
    if (-not $Manifest) { throw 'Specify -Manifest with the learning component release manifest.' }
    if (-not $Source -and -not $Download) { throw 'Specify -Source for offline assets or -Download.' }
    $spec=Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($spec.schemaVersion -ne 2 -or $spec.platform -ne 'windows-x86_64' -or $spec.backend -ne 'vulkan') {
        throw 'Incompatible learning manifest.'
    }
    $parent=Join-Path $state 'trainer'
    $identity=(Get-FileHash -LiteralPath $Manifest -Algorithm SHA256).Hash.ToLower().Substring(0,16)
    $final=Join-Path $parent $identity
    if (Test-Path -LiteralPath (Join-Path $final 'component.json')) { return (Join-Path $final 'component.json') }
    $temporary=Join-Path $parent ('.install-'+[guid]::NewGuid().ToString('N'))
    $unpacked=Join-Path $temporary 'component'
    [IO.Directory]::CreateDirectory($unpacked) | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    try {
        foreach ($asset in $spec.assets) {
            if ([IO.Path]::GetFileName($asset.filename) -cne $asset.filename -or $asset.filename -notlike '*.zip') { throw 'Invalid asset filename.' }
            $archive=Join-Path $temporary $asset.filename
            if ($Source) { [IO.File]::Copy((Join-Path $Source $asset.filename),$archive) }
            else {
                if ($asset.url -notlike 'https://*') { throw 'Downloads require HTTPS.' }
                Invoke-WebRequest -UseBasicParsing -Uri $asset.url -OutFile $archive
            }
            if ((Get-Item -LiteralPath $archive).Length -ne $asset.size -or
                (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLower() -ne $asset.sha256) { throw 'Learning asset checksum mismatch.' }
            $zip=[IO.Compression.ZipFile]::OpenRead($archive)
            try {
                foreach ($entry in $zip.Entries) {
                    if ($entry.FullName.StartsWith('/') -or $entry.FullName.Contains('\') -or $entry.FullName.Contains(':') -or
                        $entry.FullName.Split('/') -contains '..' -or (($entry.ExternalAttributes -shr 16) -band 61440) -eq 40960) { throw 'Unsafe archive entry.' }
                }
            } finally { $zip.Dispose() }
            # Relative arguments also avoid tar's ANSI argv conversion of Unicode paths.
            Push-Location -LiteralPath $temporary
            try {
                & (Join-Path $env:SystemRoot 'System32\tar.exe') -x -k -f $asset.filename -C 'component'
                if ($LASTEXITCODE -ne 0) { throw "Learning archive extraction failed: $($asset.filename)" }
            } finally { Pop-Location }
        }
        $component=Get-Content -LiteralPath (Join-Path $unpacked 'component.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($component.base_sha256 -ne $spec.base_sha256 -or $component.backend -ne $spec.backend) { throw 'Component metadata mismatch.' }
        [IO.Directory]::Move($unpacked,$final)
    } finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Recurse -Force } }
    return (Join-Path $final 'component.json')
}

if ($MyInvocation.InvocationName -ne '.') {
    $request=@{op='learning';action=$Action}
    if ($Action -eq 'reset') {
        if (-not $Yes) { throw 'Reset deletes personal learning data. Repeat with -Yes to confirm.' }
        $request.confirm=$true
    }
    if ($Action -eq 'install') {
        $request.manifest=Install-LearningComponent
    }
    Invoke-BeamRequest $request | ConvertTo-Json -Depth 12
}
