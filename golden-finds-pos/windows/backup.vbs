' Nightly backup, run by Windows Task Scheduler. Runs hidden.
Option Explicit

Dim shell, fso, appDir
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))

shell.CurrentDirectory = appDir
shell.Run """" & appDir & "\.venv\Scripts\pythonw.exe"" -m flask --app run backup", 0, True
