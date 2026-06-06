var fso = new ActiveXObject("Scripting.FileSystemObject");
var scriptDir = fso.GetParentFolderName(WScript.ScriptFullName);
var shell = new ActiveXObject("WScript.Shell");
shell.CurrentDirectory = scriptDir;
var exec = shell.Exec("python build_exe.py");
while (exec.Status == 0) { WScript.Sleep(1000); }
WScript.Echo(exec.StdOut.ReadAll());
if (exec.StdErr.AtEndOfStream != true) {
    WScript.Echo("STDERR: " + exec.StdErr.ReadAll());
}
WScript.Quit(exec.ExitCode);
