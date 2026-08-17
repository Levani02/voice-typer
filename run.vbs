' Starts voice-typer with no console window.
' Double-click this file, or put a shortcut to it in the Startup folder
' (Win+R, then: shell:startup) to have it run when Windows starts.

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

projectDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = projectDir & "\.venv\Scripts\pythonw.exe"
entry = projectDir & "\main.py"

If Not fso.FileExists(pythonw) Then
    MsgBox "The virtual environment is missing." & vbCrLf & vbCrLf & _
           "Open PowerShell in this folder and run:" & vbCrLf & _
           "  & ""C:\Users\user\AppData\Local\Programs\Python\Python313\python.exe"" -m venv .venv" & vbCrLf & _
           "  .venv\Scripts\python.exe -m pip install -r requirements.txt", _
           vbExclamation, "voice-typer"
    WScript.Quit 1
End If

shell.CurrentDirectory = projectDir
shell.Run """" & pythonw & """ """ & entry & """", 0, False
