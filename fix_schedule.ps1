# Adjust conditions of the two Excaliber Windows scheduled tasks (does NOT change times, triggers or code).
#  - run even on battery, do not stop when switching to battery
#  - wake the computer from sleep to run
#  - Monitor only: run as soon as possible if a run was missed (read-only task, safe to catch up)
$names = @('Excaliber Monitor', 'Excaliber Paper Rotation')
foreach ($n in $names) {
  try {
    $t = Get-ScheduledTask -TaskName $n -ErrorAction Stop
    $t.Settings.DisallowStartIfOnBatteries = $false
    $t.Settings.StopIfGoingOnBatteries = $false
    $t.Settings.WakeToRun = $true
    if ($n -eq 'Excaliber Monitor') { $t.Settings.StartWhenAvailable = $true }
    Set-ScheduledTask -InputObject $t -ErrorAction Stop | Out-Null
    $s = (Get-ScheduledTask -TaskName $n).Settings
    Write-Host ("[OK] {0}: WakeToRun={1}  RunOnBattery={2}  StopOnBattery={3}  StartWhenAvailable={4}" -f `
      $n, $s.WakeToRun, (-not $s.DisallowStartIfOnBatteries), $s.StopIfGoingOnBatteries, $s.StartWhenAvailable)
  } catch {
    Write-Host ("[FAILED] {0}: {1}" -f $n, $_.Exception.Message)
    Write-Host "  -> try again: right-click fix_schedule.bat, Run as administrator"
  }
}
