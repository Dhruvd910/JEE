' Runs watchdog.ps1 without flashing a console window (used by the scheduled task "JEE Workbench watchdog").
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
CreateObject("WScript.Shell").Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & here & "\watchdog.ps1""", 0, False
