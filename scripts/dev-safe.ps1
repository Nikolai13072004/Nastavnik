# Run in a dedicated PowerShell process (npm run dev:safe), never dot-source.
# Windows applies limits to this launcher AND every descendant, including native workers.
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 3101
)

$ErrorActionPreference = 'Stop'
if ($MyInvocation.InvocationName -eq '.') {
    throw 'Run this script with powershell.exe -File, not dot-sourcing.'
}

Add-Type -Path (Join-Path $PSScriptRoot 'windows-dev-job.cs')
$taskJob = [WindowsDevJob]::CreateForCurrentProcess()
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$env:NODE_OPTIONS = '--max-old-space-size=1536'
$env:RAYON_NUM_THREADS = '2'
$env:UV_THREADPOOL_SIZE = '2'

Write-Output "Safe dev: Webpack, 12 processes, 4 GiB committed memory, 20% CPU maximum. Supervisor PID: $PID"
Write-Output 'Closing this dedicated launcher terminates its server and workers.'
try {
    & node.exe node_modules/next/dist/bin/next dev --webpack --hostname 127.0.0.1 --port $Port
    $taskExitCode = $LASTEXITCODE
} finally {
    # Keep the non-inherited handle alive until process exit. Windows closes it
    # on exit (including forced termination), killing only this job's processes.
    [GC]::KeepAlive($taskJob)
}
exit $taskExitCode
