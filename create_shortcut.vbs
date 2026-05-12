Set oWS = WScript.CreateObject("WScript.Shell")
sLinkFile = oWS.SpecialFolders("Desktop") & "\A.T.L.A.S.lnk"
Set oLink = oWS.CreateShortcut(sLinkFile)

' Construct absolute paths using the directory of this script's execution
currentDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)

oLink.TargetPath = currentDir & "\ATLAS_Background.vbs"
oLink.WorkingDirectory = currentDir
oLink.Description = "Launch A.T.L.A.S Artificial Intelligence"
oLink.Save

WScript.Echo "Shortcut to ATLAS created on your Desktop."
