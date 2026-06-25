# VoiceMate - global Windows hotkeys + native clipboard -> daemon in WSL2.
#
# Usage: powershell -ExecutionPolicy Bypass -File voicemate-hotkeys.ps1
# Tip:   to run hidden at login, create a shortcut in shell:startup with
#        powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File <path>\voicemate-hotkeys.ps1
#
# Does THREE things:
#  1) Registers with the daemon (POST /register) and gets a client_id. This is how
#     this consumer identifies itself - multiple listeners can coexist.
#  2) Registers Ctrl+Alt+V (clipboard) and Ctrl+Alt+A (Claude) via the Win32
#     RegisterHotKey API and fires POST /trigger. The response reports the ACTION
#     (started/stopped/restarted) - immediate feedback that the hotkey was received
#     and what it did.
#  3) Polls GET /result and sets the Windows clipboard with Set-Clipboard. In WSL2
#     the WSLg/interop clipboard bridge is unstable, so the actual clipboard writer
#     is Windows (native, reliable). After a "stopped" (transcribing), it runs an
#     active double-check and fetches the result with retries, because the clipboard
#     fetch can fail.
#
# -Scope "all" (default): listens to the result of ANY consumer (the more general
#   setting). -Scope "mine": listens only to what THIS client_id started.

param(
    [string]$DaemonUrl = "http://127.0.0.1:47821",
    [ValidateSet("all", "mine")][string]$Scope = "all"
)

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class VoiceMateHotkeys {
    [DllImport("user32.dll")] public static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);
    [DllImport("user32.dll")] public static extern bool UnregisterHotKey(IntPtr hWnd, int id);
    [DllImport("user32.dll")] public static extern bool PeekMessage(out MSG lpMsg, IntPtr hWnd, uint min, uint max, uint remove);
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
$PM_REMOVE = 0x1

function Compare-ClipText([string]$A, [string]$B) {
    if ($null -eq $A -or $null -eq $B) { return $false }
    # Tolerant of a stray \r\n that the clipboard sometimes normalizes.
    return ($A -ceq $B) -or ($A.TrimEnd("`r", "`n") -ceq $B.TrimEnd("`r", "`n"))
}

function Set-ClipboardReliable([string]$Text) {
    # Reliable delivery in TWO steps. Returns $true if the text made it to the clipboard.
    #
    # 1) ENSURE the current clipboard: Set-Clipboard sometimes "succeeds" without
    #    applying (clipboard locked by another process for an instant). We only trust
    #    the READ-BACK; we retry with a short backoff until confirmed.
    $confirmed = $false
    for ($i = 1; $i -le 5; $i++) {
        try { Set-Clipboard -Value $Text } catch {}
        Start-Sleep -Milliseconds (60 * $i)  # 60,120,180,240,300
        $cur = ""
        try { $cur = Get-Clipboard -Raw } catch {}
        if (Compare-ClipText $cur $Text) { $confirmed = $true; break }
    }
    if (-not $confirmed) { return $false }

    # 2) HISTORY (Win+V): the service coalesces rapid changes, so a single set
    #    sometimes slips by and does NOT become an entry (that was the flakiness). We
    #    give the value a moment to settle and RE-ASSERT it - a second write of the
    #    same value is another chance to be captured (the "set it twice"). Idempotent:
    #    Win+V dedupes identical consecutive entries, so it doesn't pollute.
    #    We only re-assert if the clipboard is STILL ours: if the user copied something
    #    during the pause, we respect it and don't overwrite.
    Start-Sleep -Milliseconds 250
    try { $cur = Get-Clipboard -Raw } catch { $cur = "" }
    if (Compare-ClipText $cur $Text) {
        try { Set-Clipboard -Value $Text } catch {}
        Start-Sleep -Milliseconds 250
    }
    return $true
}

# --- Consumer registration (client_id). If the daemon is old (404), we proceed
# --- without an id (legacy behavior: scope=all global).
$ClientId = $null
try {
    $ClientId = (Invoke-RestMethod -Method Post -Uri "$DaemonUrl/register" -TimeoutSec 2).client_id
} catch {}
$idLabel = if ($ClientId) { $ClientId } else { "(unregistered)" }

function Get-Query {
    $q = "scope=$Scope"
    if ($ClientId) { $q = "client_id=$ClientId&$q" }
    return $q
}

function Send-Trigger([string]$Flow) {
    try {
        $body = @{ flow = $Flow; client_id = $ClientId } | ConvertTo-Json -Compress
        $r = Invoke-RestMethod -Method Post -Uri "$DaemonUrl/trigger" -ContentType "application/json" `
            -Body $body -TimeoutSec 2
        switch ($r.action) {
            "started"   { Write-Host "[VoiceMate] > recording... (op $($r.op_seq))" }
            "stopped"   { Write-Host "[VoiceMate] # transcribing... (op $($r.op_seq))" }
            "restarted" { Write-Host "[VoiceMate] ~ restarted (op $($r.op_seq))" }
            default     { Write-Host "[VoiceMate] trigger '$Flow' received." }
        }
        return $r.action
    } catch {
        Write-Warning "[VoiceMate] Daemon offline at $DaemonUrl (run 'make run' in WSL)."
        return $null
    }
}

function Get-Result($Since = $null) {
    # With $Since, the daemon returns the NEXT unseen result (seq > since),
    # so we can drain in order without missing the intermediate ones.
    $uri = "$DaemonUrl/result?$(Get-Query)"
    if ($null -ne $Since) { $uri = "$uri&since=$Since" }
    try { return Invoke-RestMethod -Uri $uri -TimeoutSec 2 } catch { return $null }
}

function Get-Snippet([string]$Text, [int]$Max = 60) {
    # Single-line snippet for log auditing (normalizes whitespace/line breaks).
    if ([string]::IsNullOrEmpty($Text)) { return "" }
    $flat = ($Text -replace '\s+', ' ').Trim()
    if ($flat.Length -gt $Max) { return $flat.Substring(0, $Max) + [char]0x2026 }
    return $flat
}

Write-Host "[VoiceMate] Hotkeys active: Ctrl+Alt+V (clipboard) / Ctrl+Alt+A (Claude) -> $DaemonUrl"
Write-Host "[VoiceMate] Consumer $idLabel | scope=$Scope. Clipboard set by Windows. Leave this window open."

if (-not [VoiceMateHotkeys]::RegisterHotKey([IntPtr]::Zero, 1, $MOD_CONTROL -bor $MOD_ALT, [uint32][char]'V')) {
    Write-Warning "Ctrl+Alt+V is already registered by another app."
}
if (-not [VoiceMateHotkeys]::RegisterHotKey([IntPtr]::Zero, 2, $MOD_CONTROL -bor $MOD_ALT, [uint32][char]'A')) {
    Write-Warning "Ctrl+Alt+A is already registered by another app."
}

# Don't paste whatever was already in the hub at the moment the script started.
$lastSeq = -1
$r0 = Get-Result
if ($r0) { $lastSeq = $r0.seq }

# When we see "stopped"/"restarted", the daemon is about to transcribe: for a few
# seconds we poll faster (active double-check) until the result arrives.
$fastUntil = [DateTime]::MinValue

function Poll-Result {
    # Drains in ORDER: each pass takes the next unseen result (seq > lastSeq) and
    # sets it on the clipboard. This way, recordings made in sequence ALL land in the
    # history (Win+V), in the right order - instead of just the last one (the producer
    # in WSL can emit faster than we poll, and the old single slot lost the intermediate
    # ones).
    $any = $false
    while ($true) {
        $r = Get-Result $script:lastSeq
        if (-not $r -or $r.seq -le $script:lastSeq) { break }
        $script:lastSeq = $r.seq
        $any = $true
        if ($r.text) {
            $snip = Get-Snippet $r.text
            if (Set-ClipboardReliable $r.text) {
                Write-Host "[VoiceMate] clipboard confirmed (seq $($r.seq), op $($r.op_seq)): `"$snip`""
            } else {
                Write-Warning "[VoiceMate] could not confirm the clipboard (seq $($r.seq)): `"$snip`" after several attempts."
            }
        }
    }
    return $any
}

$msg = New-Object VoiceMateHotkeys+MSG
try {
    while ($true) {
        # 1) process pending hotkeys (non-blocking)
        while ([VoiceMateHotkeys]::PeekMessage([ref]$msg, [IntPtr]::Zero, 0, 0, $PM_REMOVE)) {
            if ($msg.message -eq $WM_HOTKEY) {
                $action = switch ([int]$msg.wParam) {
                    1 { Send-Trigger "clipboard" }
                    2 { Send-Trigger "claude_chat" }
                }
                if ($action -eq "stopped" -or $action -eq "restarted") {
                    $fastUntil = (Get-Date).AddSeconds(20)  # transcribing: active double-check
                }
            }
        }
        # 2) is there a new transcription/response? set the Windows clipboard.
        [void](Poll-Result)
        # Poll fast while waiting for a just-triggered result; otherwise, take it easy.
        if ((Get-Date) -lt $fastUntil) { Start-Sleep -Milliseconds 150 }
        else { Start-Sleep -Milliseconds 300 }
    }
} finally {
    [void][VoiceMateHotkeys]::UnregisterHotKey([IntPtr]::Zero, 1)
    [void][VoiceMateHotkeys]::UnregisterHotKey([IntPtr]::Zero, 2)
}
