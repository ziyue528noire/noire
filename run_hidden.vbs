' Run a .bat hidden (no console window) and wait. Used by scheduled tasks.
Set sh = CreateObject("WScript.Shell")
sh.Run """" & WScript.Arguments(0) & """", 0, True
