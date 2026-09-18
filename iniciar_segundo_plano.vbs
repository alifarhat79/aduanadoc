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

' Comando para arrancar el servidor FastAPI en segundo plano
CmdLine = """" & PythonExe & """ -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

' Ejecutar con ventana completamente invisible (0) sin esperar (False)
WshShell.CurrentDirectory = CurrentDir
WshShell.Run CmdLine, 0, False

' Enviar aviso de inicio en segundo plano
WScript.Sleep 1500
WshShell.Run "powershell -NoProfile -ExecutionPolicy Bypass -Command ""[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; $t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); $n = $t.GetElementsByTagName('text'); $n.Item(0).AppendChild($t.CreateTextNode('AduanaDoc Activo')) | Out-Null; $n.Item(1).AppendChild($t.CreateTextNode('El sistema esta vigilando Google Drive en segundo plano.')) | Out-Null; [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('AduanaDoc').Show([Windows.UI.Notifications.ToastNotification]::new($t))""", 0, False
