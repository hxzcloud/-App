Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' 获取脚本所在目录
scriptPath = fso.GetParentFolderName(WScript.ScriptFullName)

' 切换到脚本目录
WshShell.CurrentDirectory = scriptPath

' 检查pythonw是否可用
pythonwPath = "pythonw.exe"
Set objShell = CreateObject("WScript.Shell")
Set objEnv = objShell.Environment("Process")

' 尝试用pythonw启动（无控制台）
On Error Resume Next
WshShell.Run "pythonw.exe """ & scriptPath & "\main.py""", 0, False
If Err.Number <> 0 Then
    ' 如果pythonw失败，用python启动
    WshShell.Run "python.exe """ & scriptPath & "\main.py""", 0, False
End If
On Error GoTo 0

Set WshShell = Nothing
Set fso = Nothing
