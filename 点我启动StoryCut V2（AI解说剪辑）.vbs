Option Explicit
' Silent launcher for StoryCut V2.

Dim shell, fso, root, pythonExe, pythonwExe, appScript, checkCode
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

If WScript.Arguments.Named.Exists("check") Then WScript.Quit 0

root = fso.GetParentFolderName(WScript.ScriptFullName)
pythonExe = root & "\venv\Scripts\python.exe"
pythonwExe = root & "\venv\Scripts\pythonw.exe"
appScript = root & "\storycut_v2\main.py"

If Not fso.FileExists(pythonExe) Or Not fso.FileExists(pythonwExe) Then
    shell.Popup "Project virtual environment was not found." & vbCrLf & _
        "Run: python -m venv venv", 0, "StoryCut V2", 16
    WScript.Quit 1
End If

shell.CurrentDirectory = root
checkCode = shell.Run(Quote(pythonExe) & " -c " & Quote("import PySide6"), 0, True)
If checkCode <> 0 Then
    shell.Popup "StoryCut dependencies are missing." & vbCrLf & _
        "Run: venv\Scripts\python.exe -m pip install -r requirements.txt", _
        0, "StoryCut V2", 16
    WScript.Quit checkCode
End If

shell.Run Quote(pythonwExe) & " " & Quote(appScript), 0, False

Function Quote(value)
    Quote = Chr(34) & value & Chr(34)
End Function
