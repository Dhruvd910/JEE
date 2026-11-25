# JEE Workbench watchdog: Windows runs this every 5 minutes (scheduled task "JEE Workbench watchdog").
# If the live-site window (start_site.cmd) is not running, for example because it was closed, it starts it again,
# minimised. When the window is running, it does nothing: start_site.cmd already restarts the site itself.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$cmd = Join-Path $here 'start_site.cmd'
$log = Join-Path $here 'live\watchdog.log'

$running = Get-CimInstance Win32_Process -Filter "Name='cmd.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*$cmd*" -or ($_.CommandLine -like '*serve_public.py*' -and $_.CommandLine -notlike '*neet-backend*') }  # NEET's runs from its own venv

if (-not $running) {
    Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', "`"$cmd`"" -WorkingDirectory $here -WindowStyle Minimized
    New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
    Add-Content -Path $log -Value ("[{0}] JEE site was not running; started it." -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
}
