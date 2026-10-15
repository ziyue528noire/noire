# Make the two Noire scheduled tasks wake the PC from sleep and run on battery.
# Monitor (read-only) also runs as soon as possible if a run was missed.
# Called by install_schedule.bat right after the tasks are (re)created. Does not change times or code.
$names = @('Noire Paper Rotation', 'Noire Monitor')
foreach ($n in $names) {
  try {
    $t = Get-ScheduledTask -TaskName $n -ErrorAction Stop
    $t.Settings.WakeToRun = $true
    $t.Settings.DisallowStartIfOnBatteries = $false
    $t.Settings.StopIfGoingOnBatteries = $false
    if ($n -eq 'Noire Monitor') { $t.Settings.StartWhenAvailable = $true }
    Set-ScheduledTask -InputObject $t -ErrorAction Stop | Out-Null
    $s = (Get-ScheduledTask -TaskName $n).Settings
    "[OK] {0}: WakeToRun={1}  RunOnBattery={2}  StopOnBattery={3}  StartWhenAvailable={4}" -f `
      $n, $s.WakeToRun, (-not $s.DisallowStartIfOnBatteries), $s.StopIfGoingOnBatteries, $s.StartWhenAvailable
  } catch {
    "[FAILED] {0}: {1}" -f $n, $_.Exception.Message
  }
}
"--- wake timers in current power plan (read only, AC/DC index: 1 = enabled) ---"
powercfg /q SCHEME_CURRENT SUB_SLEEP RTCWAKE | Select-String -Pattern "Current"
