param(
    [Parameter(Mandatory = $true)][int]$RootPid
)
# Prints one JSON array describing every top-level window owned by RootPid or its descendants,
# including dialog text. Used by bounded_exec.cjs so that a hidden/modal NSIS dialog
# (for example "NSIS Error: Installer integrity check has failed") is reported instead of
# silently blocking an automated run forever.

$code = @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public static class WinSnap {
    public delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc p, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr h, EnumProc p, IntPtr l);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll", CharSet = CharSet.Auto)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll", CharSet = CharSet.Auto)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);

    public static List<string[]> Snapshot(HashSet<uint> pids) {
        var rows = new List<string[]>();
        EnumWindows((h, l) => {
            uint pid; GetWindowThreadProcessId(h, out pid);
            if (!pids.Contains(pid)) return true;
            var t = new StringBuilder(512); GetWindowText(h, t, 512);
            var c = new StringBuilder(256); GetClassName(h, c, 256);
            var kids = new List<string>();
            EnumChildWindows(h, (k, l2) => {
                var s = new StringBuilder(1024); GetWindowText(k, s, 1024);
                if (s.Length > 0) kids.Add(s.ToString());
                return true;
            }, IntPtr.Zero);
            rows.Add(new[] { pid.ToString(), IsWindowVisible(h).ToString(), c.ToString(), t.ToString(), string.Join(" | ", kids) });
            return true;
        }, IntPtr.Zero);
        return rows;
    }
}
'@
Add-Type -TypeDefinition $code -ErrorAction Stop

$all = Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId
$set = New-Object 'System.Collections.Generic.HashSet[uint32]'
[void]$set.Add([uint32]$RootPid)
do {
    $before = $set.Count
    foreach ($p in $all) { if ($set.Contains([uint32]$p.ParentProcessId)) { [void]$set.Add([uint32]$p.ProcessId) } }
} while ($set.Count -ne $before)

$rows = [WinSnap]::Snapshot($set) | Where-Object { $_[3] -ne '' -or $_[4] -ne '' } | ForEach-Object {
    [pscustomobject]@{ pid = [int]$_[0]; visible = ($_[1] -eq 'True'); class = $_[2]; title = $_[3]; text = $_[4] }
}
ConvertTo-Json -InputObject @($rows) -Compress
