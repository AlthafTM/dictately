Option Explicit
Dim shell, fso, folder
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetAbsolutePathName(fso.GetParentFolderName(WScript.ScriptFullName) & "\..\..")
shell.CurrentDirectory = folder
shell.Run Chr(34) & folder & "\Dictately.exe" & Chr(34), 0, False
