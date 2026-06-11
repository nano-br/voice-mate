# VoiceMate — hotkeys globais do Windows → daemon no WSL2 (alternativa sem AutoHotkey).
#
# Uso:   powershell -ExecutionPolicy Bypass -File voicemate-hotkeys.ps1
# Dica:  para rodar oculto no login, crie um atalho em shell:startup com
#        powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File <caminho>\voicemate-hotkeys.ps1
#
# Registra Ctrl+Alt+V (clipboard) e Ctrl+Alt+A (Claude) via RegisterHotKey Win32
# e faz POST /trigger no daemon (que roda dentro do WSL com `make run`).

param([string]$DaemonUrl = "http://127.0.0.1:47821")

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class VoiceMateHotkeys {
    [DllImport("user32.dll")] public static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);
    [DllImport("user32.dll")] public static extern bool UnregisterHotKey(IntPtr hWnd, int id);
    [DllImport("user32.dll")] public static extern int GetMessage(out MSG lpMsg, IntPtr hWnd, uint min, uint max);
    [StructLayout(LayoutKind.Sequential)]
    public struct MSG {
        public IntPtr hwnd; public uint message; public IntPtr wParam; public IntPtr lParam;
        public uint time; public int ptX; public int ptY;
    }
}
"@

$MOD_ALT = 0x1
$MOD_CONTROL = 0x2
$WM_HOTKEY = 0x312

if (-not [VoiceMateHotkeys]::RegisterHotKey([IntPtr]::Zero, 1, $MOD_CONTROL -bor $MOD_ALT, [uint32][char]'V')) {
    Write-Warning "Ctrl+Alt+V já está registrado por outro app."
}
if (-not [VoiceMateHotkeys]::RegisterHotKey([IntPtr]::Zero, 2, $MOD_CONTROL -bor $MOD_ALT, [uint32][char]'A')) {
    Write-Warning "Ctrl+Alt+A já está registrado por outro app."
}
Write-Host "[VoiceMate] Hotkeys ativas: Ctrl+Alt+V (clipboard) / Ctrl+Alt+A (Claude) -> $DaemonUrl"
Write-Host "[VoiceMate] Deixe esta janela aberta (Ctrl+C para sair)."

function Send-Trigger([string]$Flow) {
    try {
        $body = @{ flow = $Flow } | ConvertTo-Json -Compress
        Invoke-RestMethod -Method Post -Uri "$DaemonUrl/trigger" -ContentType "application/json" `
            -Body $body -TimeoutSec 2 | Out-Null
    } catch {
        Write-Warning "[VoiceMate] Daemon offline em $DaemonUrl (rode 'make run' no WSL)."
    }
}

try {
    $msg = New-Object VoiceMateHotkeys+MSG
    while ([VoiceMateHotkeys]::GetMessage([ref]$msg, [IntPtr]::Zero, 0, 0) -ne 0) {
        if ($msg.message -eq $WM_HOTKEY) {
            switch ([int]$msg.wParam) {
                1 { Send-Trigger "clipboard" }
                2 { Send-Trigger "claude_chat" }
            }
        }
    }
} finally {
    [void][VoiceMateHotkeys]::UnregisterHotKey([IntPtr]::Zero, 1)
    [void][VoiceMateHotkeys]::UnregisterHotKey([IntPtr]::Zero, 2)
}
