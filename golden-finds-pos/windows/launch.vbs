' Golden Finds launcher.
'
' Starts the till in the background if it is not already running, then
' opens it in the browser. With /background it only makes sure the till
' is running - that is what the computer does at startup.

Option Explicit

Dim shell, fso, appDir, i, background
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))

background = False
If WScript.Arguments.Count > 0 Then
    If LCase(WScript.Arguments(0)) = "/background" Then background = True
End If

Function TillIsUp()
    Dim http
    TillIsUp = False
    On Error Resume Next
    Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0")
    http.setTimeouts 1500, 1500, 1500, 1500
    http.open "GET", "http://127.0.0.1:8000/login", False
    http.send
    If Err.Number = 0 Then
        If http.status = 200 Then TillIsUp = True
    End If
    Err.Clear
    On Error GoTo 0
End Function

If Not TillIsUp() Then
    If Not fso.FileExists(appDir & "\.venv\Scripts\pythonw.exe") Then
        MsgBox "Golden Finds is not installed yet." & vbCrLf & _
               "Double-click 'Install Golden Finds' first.", vbExclamation, "Golden Finds"
        WScript.Quit 1
    End If

    shell.CurrentDirectory = appDir
    ' Window style 0 = hidden: no black box for anyone to close by mistake.
    shell.Run """" & appDir & "\.venv\Scripts\pythonw.exe"" """ & appDir & "\serve.py""", 0, False

    For i = 1 To 40
        WScript.Sleep 500
        If TillIsUp() Then Exit For
    Next

    If Not TillIsUp() And Not background Then
        MsgBox "The till did not start." & vbCrLf & _
               "Restart the computer. If it still does not open, call Iman." & vbCrLf & vbCrLf & _
               "Details are in: " & appDir & "\instance\server.log", vbExclamation, "Golden Finds"
        WScript.Quit 1
    End If
End If

If Not background Then shell.Run "http://localhost:8000/"
