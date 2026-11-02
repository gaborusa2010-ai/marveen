' Starts marveen-or.ps1 with no console window, so the 5-minute task never
' flashes a window over a game (#749).
Set sh = CreateObject("WScript.Shell")
dir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & dir & "\marveen-or.ps1""", 0, False
