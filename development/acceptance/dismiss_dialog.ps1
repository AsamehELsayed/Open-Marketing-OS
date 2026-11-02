param(
    [int]$timeoutSeconds = 25
)

$logFile = "$env:TEMP\dismiss_dialog.log"
"Starting dialog clicker with timeout $timeoutSeconds at $(Get-Date)" | Out-File $logFile -Append -Encoding utf8

$code = @'
using System;
using System.Runtime.InteropServices;
using System.Text;

public class DialogClicker {
    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern bool EnumChildWindows(IntPtr hWnd, EnumWindowsProc lpEnumFunc, IntPtr lParam);

    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
    public static extern int GetClassName(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

    [DllImport("user32.dll")]
    public static extern IntPtr SendMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);

    public const uint BM_CLICK = 0x00F5;

    public static int ClickMatchingButtons() {
        int clicked = 0;
        EnumWindows((hWnd, lParam) => {
            StringBuilder sbTitle = new StringBuilder(256);
            GetWindowText(hWnd, sbTitle, 256);
            StringBuilder sbClass = new StringBuilder(256);
            GetClassName(hWnd, sbClass, 256);
            string title = sbTitle.ToString();
            string cls = sbClass.ToString();

            if (cls == "#32770" || title.Contains("Open Marketing OS") || title.Contains("Save")) {
                EnumChildWindows(hWnd, (childHwnd, childParam) => {
                    StringBuilder cTitle = new StringBuilder(256);
                    GetWindowText(childHwnd, cTitle, 256);
                    StringBuilder cClass = new StringBuilder(256);
                    GetClassName(childHwnd, cClass, 256);
                    string btnText = cTitle.ToString();

                    if (btnText.Contains("Retry") || btnText.Contains("Save")) {
                        SendMessage(childHwnd, BM_CLICK, IntPtr.Zero, IntPtr.Zero);
                        clicked++;
                    }
                    return true;
                }, IntPtr.Zero);
            }
            return true;
        }, IntPtr.Zero);
        return clicked;
    }
}
'@

try {
    Add-Type -TypeDefinition $code -ErrorAction SilentlyContinue
} catch {}

$deadline = (Get-Date).AddSeconds($timeoutSeconds)
while ((Get-Date) -lt $deadline) {
    $count = [DialogClicker]::ClickMatchingButtons()
    if ($count -gt 0) {
        "Clicked $count button(s) at $(Get-Date)" | Out-File $logFile -Append -Encoding utf8
        Start-Sleep -Milliseconds 500
    }
    Start-Sleep -Milliseconds 300
}
