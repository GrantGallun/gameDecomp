Option Explicit
Dim shell, command, result
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = "C:\Code\gameDecomp"
command = """C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"" -NoProfile -NonInteractive -WindowStyle Hidden -File ""C:\Code\gameDecomp\eval\campaign_hourly.ps1"""
result = shell.Run(command, 0, True)
WScript.Quit result
