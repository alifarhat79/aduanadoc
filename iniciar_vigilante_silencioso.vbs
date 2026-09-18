Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")

' Obtener directorio del script
CurrentDir = FSO.GetParentFolderName(WScript.ScriptFullName)

' Buscar ejecutable de Python
PythonExe = "python.exe"
If FSO.FileExists("C:\Python314\python.exe") Then
    PythonExe = "C:\Python314\python.exe"
ElseIf FSO.FileExists("C:\Python312\python.exe") Then
    PythonExe = "C:\Python312\python.exe"
ElseIf FSO.FileExists("C:\Python311\python.exe") Then
    PythonExe = "C:\Python311\python.exe"
End If

' Comando para arrancar el vigilante en segundo plano SIN SERVIDOR
CmdLine = """" & PythonExe & """ vigilante_background.py"

' Ejecutar con ventana completamente oculta (0) sin esperar (False)
WshShell.CurrentDirectory = CurrentDir
WshShell.Run CmdLine, 0, False
