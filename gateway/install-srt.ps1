# Uses the installed Python, FFmpeg and NDI runtime. Run as the playout user, elevated.
[CmdletBinding()]
param(
    [ValidateSet('Install', 'Start', 'Stop', 'Status')][string]$Action = 'Status',
    [string]$Directory = "$env:ProgramData\OBE-NDI-Gateway",
    [string]$Source = 'PLAYOUT (PO_Uno4)',
    [ValidateRange(1, 65535)][int]$Port = 20879,
    [string]$FFmpeg = 'C:\ffmpeg\bin\ffmpeg.exe',
    [string]$NDIRuntime = 'C:\OndaMultimedia\x64\NDI\Processing.NDI.Lib.x64.dll'
)
$ErrorActionPreference = 'Stop'
$TaskName = 'OBE-NDI-SRT-Gateway'
$Marker = Join-Path $Directory 'control.txt'
$KeyFile = Join-Path $Directory 'srt-passphrase.txt'
$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($Action -eq 'Install') {
    if ($Task -and $Task.State -eq 'Running') { throw 'Stop the existing gateway before updating it.' }
    $Python = (Get-Command python.exe -ErrorAction Stop).Source
    foreach ($File in @($Python, $FFmpeg, $NDIRuntime, (Join-Path $PSScriptRoot 'ndi_sender.py'))) {
        if (-not (Test-Path -LiteralPath $File -PathType Leaf)) { throw 'A required local executable or source is missing.' }
    }
    # Paths and source are quoted argv fields, never shell expressions.
    foreach ($Value in @($Directory, $Source, $FFmpeg, $NDIRuntime)) {
        if ($Value.Contains('"') -or $Value.Contains("`n") -or $Value.Contains("`r")) { throw 'Invalid argument.' }
    }
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $Grants = @("*$($Identity.User.Value):(OI)(CI)F", '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F')
    & icacls.exe $Directory /inheritance:r /grant:r $Grants | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not restrict access to the gateway directory.' }
    foreach ($Name in @('ndi_sender.py', 'install-srt.ps1')) {
        $From = Join-Path $PSScriptRoot $Name
        $To = Join-Path $Directory $Name
        if ([IO.Path]::GetFullPath($From) -ne [IO.Path]::GetFullPath($To)) { Copy-Item -LiteralPath $From -Destination $To -Force }
    }
    if (-not (Test-Path -LiteralPath $KeyFile)) {
        $Bytes = New-Object byte[] 32
        $Random = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $Random.GetBytes($Bytes) } finally { $Random.Dispose() }
        [IO.File]::WriteAllText($KeyFile, ([BitConverter]::ToString($Bytes).Replace('-', '').ToLowerInvariant()))
    }
    if ([IO.File]::ReadAllText($KeyFile).Trim() -cnotmatch '^[A-Za-z0-9_-]{10,79}$') { throw 'Invalid SRT key file.' }
    $Arguments = '"{0}\ndi_sender.py" --source "{1}" --ffmpeg "{2}" --ndi-runtime "{3}" --srt-port {4} --srt-passphrase-file "{0}\srt-passphrase.txt" --stop-file "{0}\control.txt" --log-file "{0}\gateway.log"' -f $Directory, $Source, $FFmpeg, $NDIRuntime, $Port
    $Run = New-ScheduledTaskAction -Execute $Python -Argument $Arguments -WorkingDirectory $Directory
    $Trigger = New-ScheduledTaskTrigger -AtLogOn -User $Identity.Name
    $Principal = New-ScheduledTaskPrincipal -UserId $Identity.Name -LogonType Interactive -RunLevel Limited
    $Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $TaskName -Action $Run -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null
    # Only the gateway's existing FFmpeg gets this inbound UDP rule.
    if (Get-NetFirewallRule -Name $TaskName -ErrorAction SilentlyContinue) {
        Get-NetFirewallRule -Name $TaskName | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -Protocol UDP -LocalPort $Port | Out-Null
        Get-NetFirewallRule -Name $TaskName | Get-NetFirewallApplicationFilter | Set-NetFirewallApplicationFilter -Program $FFmpeg | Out-Null
        Set-NetFirewallRule -Name $TaskName -Enabled True -Direction Inbound -Action Allow -Profile Any | Out-Null
    } else {
        New-NetFirewallRule -Name $TaskName -DisplayName 'OBE NDI SRT gateway' -Direction Inbound -Action Allow -Protocol UDP -LocalPort $Port -Program $FFmpeg -Profile Any | Out-Null
    }
    $Action = 'Start'
}

if ($Action -eq 'Start') {
    [IO.File]::WriteAllText($Marker, 'run')
    Start-ScheduledTask -TaskName $TaskName
} elseif ($Action -eq 'Stop') {
    [IO.File]::WriteAllText($Marker, 'stop')
    $Deadline = (Get-Date).AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 250
        $Task = Get-ScheduledTask -TaskName $TaskName
    } while ($Task.State -eq 'Running' -and (Get-Date) -lt $Deadline)
    if ($Task.State -eq 'Running') { throw 'Gateway stop timed out; inspect gateway.log before taking further action.' }
}
$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$Endpoints = @(Get-NetUDPEndpoint -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object LocalAddress, LocalPort, OwningProcess)
[pscustomobject]@{ Task = $TaskName; State = if ($Task) { [string]$Task.State } else { 'Not installed' }; Port = $Port; Directory = $Directory; Endpoints = $Endpoints } | ConvertTo-Json -Depth 4 -Compress
