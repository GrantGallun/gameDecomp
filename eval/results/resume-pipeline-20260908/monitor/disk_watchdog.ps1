# Pause the campaign (durable service pause) if C: free space falls below a floor.
# Checks every 5 minutes; logs every check; exits after pausing once.
param([double]$FloorGB = 25, [int]$IntervalSeconds = 300)
$log = Join-Path $PSScriptRoot 'disk-watchdog.log'
while ($true) {
    $free = (Get-PSDrive C).Free / 1GB
    $line = "{0:yyyy-MM-dd HH:mm:ss} C free {1:N1} GB (floor {2} GB)" -f (Get-Date), $free, $FloorGB
    Add-Content -Path $log -Value $line
    if ($free -lt $FloorGB) {
        Add-Content -Path $log -Value "  below floor: requesting campaign pause"
        $out = & wsl.exe -d Ubuntu -e /home/grant/decomp/sbk1/.venv/bin/python /mnt/c/Code/gameDecomp/eval/campaign_service.py --run /mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908 pause
        Add-Content -Path $log -Value ("  pause result: " + ($out -join ' '))
        exit 0
    }
    Start-Sleep -Seconds $IntervalSeconds
}
