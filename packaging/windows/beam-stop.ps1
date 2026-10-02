# SPDX-License-Identifier: Apache-2.0
param([Parameter(Mandatory=$true)][string]$InstallDir)
$ErrorActionPreference = 'Stop'
$exe = [IO.Path]::GetFullPath((Join-Path $InstallDir 'beamd.exe'))
try {
    $processes = @(Get-Process beamd -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe })
    foreach ($process in $processes) {
        if (-not $process.HasExited) {
            Stop-Process -InputObject $process -Force -ErrorAction SilentlyContinue
            if (-not $process.WaitForExit(10000)) { throw 'Beam did not stop before upgrade.' }
        }
    }
    exit 0
} catch { Write-Error $_ -ErrorAction Continue; exit 1 }
