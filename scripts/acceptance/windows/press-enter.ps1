param([int]$ProcessId)
Add-Type -AssemblyName Microsoft.VisualBasic
Add-Type -AssemblyName System.Windows.Forms
$ok = $false
try { [Microsoft.VisualBasic.Interaction]::AppActivate($ProcessId); $ok = $true } catch { $ok = $false }
Start-Sleep -Milliseconds 700
if ($ok) { [System.Windows.Forms.SendKeys]::SendWait('{ENTER}') }
"activated=$ok"
