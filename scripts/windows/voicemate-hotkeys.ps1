# VoiceMate - global Windows hotkeys + native clipboard -> daemon in WSL2.
#
# Usage: powershell -ExecutionPolicy Bypass -File voicemate-hotkeys.ps1 [-Scope all|mine] [-DaemonUrl <url>] [-NoToast]
#                   [-Language auto|pt-BR|en|es]
# Tip:   to run hidden at login, create a shortcut in shell:startup with
#        powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File <path>\voicemate-hotkeys.ps1
#
# What it does:
#  1) Registers with the daemon (POST /register) and gets a client_id. This is how
#     this consumer identifies itself - multiple listeners can coexist. If the daemon
#     is not up yet, registration is retried every 5 s (logged once when it works);
#     a trigger sent while unregistered adopts the id the daemon mints for it.
#  2) Registers Ctrl+Alt+V (clipboard) and Ctrl+Alt+A (Claude) via the Win32
#     RegisterHotKey API (MOD_NOREPEAT: holding the keys fires once, it does not
#     toggle start/stop repeatedly) and fires POST /trigger. The response reports
#     the ACTION (started/stopped/restarted) - immediate feedback that the hotkey was
#     received and what it did. Failures get distinct messages: daemon offline (connection
#     refused: start it), daemon busy (no answer in time) or daemon error (HTTP 5xx:
#     see the WSL console).
#  3) Polls GET /result?since=<seq> and drains the results IN ORDER into the Windows
#     clipboard (Set-Clipboard + read-back). In WSL2 the WSLg/interop clipboard bridge
#     is unstable, so the actual clipboard writer is Windows (native, reliable). After
#     a "stopped" it polls faster until the result arrives. Results that already
#     existed when the script first reached the daemon are not pasted. A text the
#     clipboard refuses is kept and retried (3 more tries, 1 s apart, without
#     blocking the hotkeys) before any later result; if it still fails, a toast says
#     so and the full text is printed in the console to copy by hand.
#     Every /result (and /health) carries the daemon `instance`, random per daemon
#     process: a new instance means the daemon restarted and its seq started again
#     from 0, so the drain cursor is reset (otherwise every new result would be
#     skipped until seq caught up) and the script registers again.
#  4) Error events (a result with `error` set, e.g. "mic_unavailable", empty text)
#     never touch the clipboard: the daemon message is shown plus a diagnosis from the
#     Windows Core Audio API - no active capture device -> connect a microphone; a
#     microphone is there -> WSL cannot open it, the WSLg audio bridge is stuck: run
#     `wsl --shutdown` and start the daemon again.
#  5) Checks GET /health every 10 s and warns once when the daemon reports WSL audio
#     "down" (only `wsl --shutdown` fixes it) and once when it recovers. At startup it
#     warns if Windows has no active microphone.
# Actionable warnings are also shown as Windows toast notifications (Windows
# PowerShell 5.1; pwsh 7 has no WinRT projection, so it stays console only).
# -NoToast keeps them in the console.
#
# All HTTP goes through Invoke-Daemon (HttpWebRequest, no proxy, explicit timeouts).
# On Windows a REFUSED loopback connection only fails after ~2 s (SYN retries), the
# same order as the request timeout, so a short TCP probe runs before triggers, and
# while the daemon is unreachable, to tell "offline" from "busy" quickly.
#
# -Scope "all" (default): listens to the result of ANY consumer (the more general
#   setting). -Scope "mine": listens only to what THIS client_id started.
#
# -Language "auto" (default): $env:VOICEMATE_LANG set on WINDOWS if any, else the
#   language the daemon reports in /health (so the console never mixes the
#   daemon's messages with another language), else the Windows display language;
#   pt* -> pt-BR, es* -> es, anything else -> en. Messages are translated with the
#   project's gettext catalogs.

param(
    [string]$DaemonUrl = "http://127.0.0.1:47821",
    [ValidateSet("all", "mine")][string]$Scope = "all",
    [switch]$NoToast,
    [ValidateSet("auto", "pt-BR", "en", "es")][string]$Language = "auto"
)

# --- Localization -----------------------------------------------------------------
# User-facing messages are English msgids looked up in the gettext catalogs the
# daemon uses (app/i18n/locales/<lang>/LC_MESSAGES/voicemate.po, read at runtime),
# so this file stays ASCII and never carries translations itself. Placeholders use
# .NET composite format ({0}, {1}) filled in with -f. Every message must be a
# single-quoted literal passed to the lookup function, and must also be listed in
# app/i18n/windows_script_messages.py (that is how pybabel extracts it);
# tests/test_windows_script_i18n.py checks both. en needs no catalog: the msgid is
# the English text.

function ConvertTo-CatalogLocale([string]$Name) {
    # "pt-BR" / "pt_BR" / "pt_BR.UTF-8" -> pt_BR; "es" / "es-MX" / "es_419" -> es;
    # anything else (or nothing) -> en.
    $n = ([string]$Name).Trim().ToLowerInvariant()
    if ($n -match '^pt([-_.@]|$)') { return "pt_BR" }
    if ($n -match '^es([-_.@]|$)') { return "es" }
    return "en"
}

function ConvertFrom-PoString([string]$Quoted) {
    # Body of a "..." .po string -> text: \n \t \r \" \\ (any other \x -> x).
    return [regex]::Replace($Quoted, '\\(.)', {
        param($m)
        switch -CaseSensitive ($m.Groups[1].Value) {
            "n" { "`n" }
            "t" { "`t" }
            "r" { "`r" }
            default { $m.Groups[1].Value }
        }
    })
}

function Add-PoEntry($Catalog, $Entry) {
    # Keeps only usable translations: not fuzzy, no context, not plural, msgid and
    # msgstr both non-empty (an empty msgid is the catalog header).
    if ($null -eq $Entry -or $Entry.Fuzzy) { return }
    $fields = $Entry.Fields
    if ($fields.Contains("msgctxt") -or $fields.Contains("msgid_plural")) { return }
    $id = [string]$fields["msgid"]
    $str = [string]$fields["msgstr"]
    if ($id -eq "" -or $str -eq "") { return }
    $Catalog[$id] = $str
}

function Read-PoCatalog([string]$Path) {
    # Minimal gettext .po reader -> Dictionary msgid -> msgstr (case-sensitive keys).
    # Handles multi-line strings (consecutive "..." lines are concatenated), escape
    # sequences, comments, the #, fuzzy flag and obsolete (#~) entries. The file is
    # read as UTF-8, which is how Babel writes it.
    $catalog = New-Object 'System.Collections.Generic.Dictionary[string,string]'
    $entry = $null   # entry being read: @{ Fuzzy; HasStr; Fields = @{ keyword = text } }
    $field = $null   # keyword that a "..." continuation line belongs to
    foreach ($raw in [System.IO.File]::ReadAllLines($Path, [System.Text.Encoding]::UTF8)) {
        $line = $raw.Trim()
        if ($line -eq "") {
            Add-PoEntry $catalog $entry
            $entry = $null; $field = $null
            continue
        }
        if ($line.StartsWith("#")) {
            $field = $null
            # A comment after a msgstr already belongs to the next entry.
            if ($null -ne $entry -and $entry.HasStr) { Add-PoEntry $catalog $entry; $entry = $null }
            if ($line -match '^#,.*\bfuzzy\b') {
                if ($null -eq $entry) { $entry = @{ Fuzzy = $false; HasStr = $false; Fields = @{} } }
                $entry.Fuzzy = $true
            }
            continue
        }
        if ($line -match '^(msgctxt|msgid_plural|msgid|msgstr(?:\[\d+\])?)\s+"(.*)"$') {
            $keyword = $Matches[1]
            $text = $Matches[2]
            if ($text.Contains('\')) { $text = ConvertFrom-PoString $text }
            # A msgctxt/msgid after a msgstr starts the next entry, even without a blank line.
            if ($null -ne $entry -and $entry.HasStr -and ($keyword -eq "msgctxt" -or $keyword -eq "msgid")) {
                Add-PoEntry $catalog $entry
                $entry = $null
            }
            if ($null -eq $entry) { $entry = @{ Fuzzy = $false; HasStr = $false; Fields = @{} } }
            if ($keyword.StartsWith("msgstr")) { $entry.HasStr = $true }
            $entry.Fields[$keyword] = $text
            $field = $keyword
            continue
        }
        if ($null -ne $field -and $line -match '^"(.*)"$') {
            $text = $Matches[1]
            if ($text.Contains('\')) { $text = ConvertFrom-PoString $text }
            $entry.Fields[$field] += $text
        }
        # Anything else is not valid .po syntax: ignored.
    }
    Add-PoEntry $catalog $entry
    return , $catalog
}

function Set-UiLanguage([string]$Name) {
    # Loads the catalog for $Name (any pt*/es*/other spelling). A missing or
    # unreadable catalog leaves the messages in English, with a visible warning.
    $script:UiLocale = ConvertTo-CatalogLocale $Name
    $script:Catalog = $null
    if ($script:UiLocale -eq "en") { return }
    try {
        $po = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine(
                $PSScriptRoot, "..", "..", "app", "i18n", "locales", $script:UiLocale, "LC_MESSAGES", "voicemate.po"))
        $script:Catalog = Read-PoCatalog $po
    } catch {
        $script:Catalog = $null
        # English on purpose: the catalog is exactly what failed to load (e.g. the
        # script was copied out of the repo, away from app/i18n/locales).
        Write-Warning "[VoiceMate] $($script:UiLocale) messages not loaded ($($_.Exception.Message)); using English."
    }
}

function Initialize-Localization([string]$Requested) {
    # Language: -Language > $env:VOICEMATE_LANG (Windows side) > Windows display
    # language. In the last case the script later follows the daemon's language
    # (Update-LanguageFromDaemon), since it relays the daemon's own messages.
    $script:FollowDaemonLang = $false
    if ($Requested -and $Requested -ne "auto") { $name = $Requested }
    elseif ($env:VOICEMATE_LANG) { $name = $env:VOICEMATE_LANG }
    else {
        $script:FollowDaemonLang = $true
        try { $name = (Get-UICulture).Name } catch { $name = "" }
    }
    Set-UiLanguage $name
}

function Update-LanguageFromDaemon($Health) {
    # Auto mode with no explicit choice: speak the language the daemon reports, so
    # its relayed messages (e.g. "Microphone unavailable") match ours.
    if (-not $script:FollowDaemonLang -or $null -eq $Health -or -not $Health.lang) { return }
    if ((ConvertTo-CatalogLocale ([string]$Health.lang)) -ne $script:UiLocale) { Set-UiLanguage ([string]$Health.lang) }
}

function Get-FormatItems([string]$Text) {
    # The {n} / {n:fmt} format items of $Text, sorted (a translation may reorder them).
    return (@([regex]::Matches($Text, '\{\d+(?:,-?\d+)?(?::[^{}]*)?\}') | ForEach-Object { $_.Value }) | Sort-Object) -join "|"
}

function Get-LocalizedText([string]$MsgId) {
    # The msgid in the active language, or the msgid itself when there is no usable
    # translation. A translation whose format items differ from the msgid's, or that
    # is not a valid .NET format string, is ignored: a broken catalog entry must
    # never make the -f that follows throw (that would end the hotkey loop).
    if ($null -eq $script:Catalog) { return $MsgId }
    $text = $null
    if (-not $script:Catalog.TryGetValue($MsgId, [ref]$text)) { return $MsgId }
    if ((Get-FormatItems $text) -cne (Get-FormatItems $MsgId)) { return $MsgId }
    try { [void][string]::Format($text, [object[]](1..10)) } catch { return $MsgId }
    return $text
}

Initialize-Localization $Language

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

# Windows Core Audio (MMDevice API): how many ACTIVE capture endpoints Windows has.
# Only the vtable slots we call are declared, in order (COM dispatches by position).
$script:CoreAudioReady = $false
try {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class VoiceMateCoreAudio {
    [ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IMMDeviceEnumerator {
        [PreserveSig] int EnumAudioEndpoints(int dataFlow, int stateMask, out IMMDeviceCollection devices);
    }
    [ComImport, Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IMMDeviceCollection {
        [PreserveSig] int GetCount(out uint count);
    }
    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    private class MMDeviceEnumerator { }

    private const int eCapture = 1;
    private const int DEVICE_STATE_ACTIVE = 0x1;

    // Number of active microphones (capture endpoints), or -1 if the query failed.
    public static int GetActiveCaptureCount() {
        IMMDeviceEnumerator enumerator = null;
        IMMDeviceCollection devices = null;
        try {
            enumerator = (IMMDeviceEnumerator)(new MMDeviceEnumerator());
            if (enumerator.EnumAudioEndpoints(eCapture, DEVICE_STATE_ACTIVE, out devices) != 0 || devices == null) return -1;
            uint count;
            if (devices.GetCount(out count) != 0) return -1;
            return (int)count;
        } finally {
            if (devices != null) Marshal.ReleaseComObject(devices);
            if (enumerator != null) Marshal.ReleaseComObject(enumerator);
        }
    }
}
'@
    $script:CoreAudioReady = $true
} catch {
    Write-Warning ((Get-LocalizedText '[VoiceMate] Windows audio device check unavailable: {0}') -f $_.Exception.Message)
}

# Non-blocking "is the clipboard free?" check (see Test-ClipboardFree).
$script:ClipboardProbeReady = $false
try {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class VoiceMateClipboard {
    [DllImport("user32.dll")] public static extern bool OpenClipboard(IntPtr hWndNewOwner);
    [DllImport("user32.dll")] public static extern bool CloseClipboard();
}
'@
    $script:ClipboardProbeReady = $true
} catch {}

$MOD_ALT = 0x1
$MOD_CONTROL = 0x2
# Without MOD_NOREPEAT, HOLDING the keys auto-repeats WM_HOTKEY and toggles
# start/stop over and over (a recording that stops at once with "no audio").
$MOD_NOREPEAT = 0x4000
$WM_HOTKEY = 0x312
$PM_REMOVE = 0x1

$DaemonUrl = $DaemonUrl.TrimEnd("/")
$DaemonEndpoint = [Uri]$DaemonUrl

$ProbeTimeoutMs = 500        # TCP probe: a listening loopback port accepts in << 1 ms
$TriggerTimeoutMs = 3000     # the daemon answers /trigger right away (mic opens async)
$RequestTimeoutMs = 2000     # register / result / health
$RegisterRetrySec = 5        # lazy (re)registration while unregistered
$HealthIntervalSec = 10      # /health watch while connected
$ReconnectIntervalSec = 2    # probe + /health while the daemon is unreachable
$ClipboardRetryMax = 3       # extra delivery attempts for a text the clipboard refused
$ClipboardRetrySec = 1       # spacing between those attempts (the loop keeps running)
$ToastAppId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'

$script:ClientId = $null
$script:DaemonOnline = $null      # $null = not contacted yet; $true / $false afterwards
$script:DaemonInstance = $null    # instance of the daemon the drain cursor belongs to ("" = legacy daemon)
$script:LastSeq = $null           # drain cursor; $null = no baseline yet
$script:LastHealthResult = $null  # last /health outcome (Send-Trigger reuses it while offline)
$script:AudioState = $null        # last WSL audio health seen: "ok" / "down"
$script:NextRegisterAt = [DateTime]::MinValue
$script:NextHealthAt = [DateTime]::MinValue
$script:NextReconnectAt = [DateTime]::MinValue
$script:ToastAvailable = -not $NoToast
$script:LastToastKey = ""
$script:LastToastAt = [DateTime]::MinValue
$script:PendingClip = $null       # text result whose clipboard delivery is being retried

# Windows PowerShell sends "Expect: 100-continue" on POST and then waits ~350 ms for
# an answer the daemon (HTTP/1.0) never sends. Turn it off.
try { [System.Net.ServicePointManager]::Expect100Continue = $false } catch {}

function Compare-ClipText([string]$A, [string]$B) {
    if ($null -eq $A -or $null -eq $B) { return $false }
    # Tolerant of a stray \r\n that the clipboard sometimes normalizes.
    return ($A -ceq $B) -or ($A.TrimEnd("`r", "`n") -ceq $B.TrimEnd("`r", "`n"))
}

function Test-ClipboardFree {
    # Non-blocking check that no other process holds the clipboard open. In Windows
    # PowerShell, Set-Clipboard and Get-Clipboard each retry for ~1 s on a locked
    # clipboard, which would freeze the hotkey loop; this lets us skip that attempt.
    # Opening and closing without writing does not change the clipboard.
    if (-not $script:ClipboardProbeReady) { return $true }
    try {
        if (-not [VoiceMateClipboard]::OpenClipboard([IntPtr]::Zero)) { return $false }
        [void][VoiceMateClipboard]::CloseClipboard()
    } catch {}
    return $true
}

function Set-ClipboardReliable([string]$Text, [int]$Attempts = 5) {
    # Reliable delivery in TWO steps. Returns $true if the text made it to the clipboard.
    #
    # 1) ENSURE the current clipboard: Set-Clipboard sometimes "succeeds" without
    #    applying (clipboard locked by another process for an instant). We only trust
    #    the READ-BACK; we retry with a short backoff until confirmed. An attempt on a
    #    clipboard another process holds open is skipped instead of waited on.
    $confirmed = $false
    for ($i = 1; $i -le $Attempts; $i++) {
        if (Test-ClipboardFree) {
            try { Set-Clipboard -Value $Text } catch {}
        }
        Start-Sleep -Milliseconds (60 * $i)  # 60,120,180,240,300
        $cur = ""
        if (Test-ClipboardFree) {
            try { $cur = Get-Clipboard -Raw } catch {}
        }
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
    $cur = ""
    if (Test-ClipboardFree) {
        try { $cur = Get-Clipboard -Raw } catch { $cur = "" }
    }
    if (Compare-ClipText $cur $Text) {
        try { Set-Clipboard -Value $Text } catch {}
        Start-Sleep -Milliseconds 250
    }
    return $true
}

function Get-Snippet([string]$Text, [int]$Max = 60) {
    # Single-line snippet for log auditing (normalizes whitespace/line breaks).
    if ([string]::IsNullOrEmpty($Text)) { return "" }
    $flat = ($Text -replace '\s+', ' ').Trim()
    if ($flat.Length -gt $Max) { return $flat.Substring(0, $Max) + [char]0x2026 }
    return $flat
}

# --- Notifications ------------------------------------------------------------

function Show-Toast([string]$Title, [string]$Body) {
    # Best effort. WinRT toasts work in Windows PowerShell 5.1; pwsh 7 has no WinRT
    # projection, so the first failure turns toasts off for good (console only). The
    # same toast within 10 s is shown once.
    if (-not $script:ToastAvailable) { return }
    $key = "$Title|$Body"
    $now = Get-Date
    if ($key -eq $script:LastToastKey -and ($now - $script:LastToastAt).TotalSeconds -lt 10) { return }
    try {
        $null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
        $null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
        $t = [System.Security.SecurityElement]::Escape($Title)
        $b = [System.Security.SecurityElement]::Escape($Body)
        $doc = New-Object Windows.Data.Xml.Dom.XmlDocument
        $doc.LoadXml("<toast><visual><binding template=`"ToastGeneric`"><text>$t</text><text>$b</text></binding></visual></toast>")
        $toast = New-Object Windows.UI.Notifications.ToastNotification $doc
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($ToastAppId).Show($toast)
        $script:LastToastKey = $key
        $script:LastToastAt = $now
    } catch {
        $script:ToastAvailable = $false
    }
}

function Write-Alert([string]$Title, [string]$Text) {
    # Actionable warning: console + toast.
    Write-Warning "[VoiceMate] $Text"
    Show-Toast "VoiceMate: $Title" $Text
}

function Get-ActiveMicCount {
    # Active capture endpoints on Windows, or -1 if it could not be determined.
    if (-not $script:CoreAudioReady) { return -1 }
    try { return [VoiceMateCoreAudio]::GetActiveCaptureCount() } catch { return -1 }
}

# --- Daemon HTTP ----------------------------------------------------------------

function Test-DaemonPort {
    # Fast reachability check. A refused loopback connect takes ~2 s on Windows (SYN
    # retries), while a listening port accepts almost instantly: not connected within
    # $ProbeTimeoutMs means nothing is listening.
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $ar = $client.BeginConnect($DaemonEndpoint.DnsSafeHost, $DaemonEndpoint.Port, $null, $null)
        if (-not $ar.AsyncWaitHandle.WaitOne($ProbeTimeoutMs)) { return $false }
        $client.EndConnect($ar)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Set-DaemonReachable([bool]$Reachable, [switch]$Quiet) {
    # Tracks whether the daemon answers; logs only transitions (not the first contact).
    $prev = $script:DaemonOnline
    $script:DaemonOnline = $Reachable
    # Just (re)connected: if still unregistered, register right away, not in 5 s.
    if ($Reachable -and $prev -ne $true) { $script:NextRegisterAt = [DateTime]::MinValue }
    if ($null -eq $prev -or $prev -eq $Reachable -or $Quiet) { return }
    if ($Reachable) {
        Write-Host ((Get-LocalizedText '[VoiceMate] Connected to the daemon at {0}.') -f $DaemonUrl)
    } else {
        Write-Warning ((Get-LocalizedText '[VoiceMate] Lost contact with the daemon at {0} (stopped or restarting?). Retrying every {1} s.') -f $DaemonUrl, $ReconnectIntervalSec)
    }
}

function Read-HttpBody($Response) {
    $reader = New-Object System.IO.StreamReader($Response.GetResponseStream(), [System.Text.Encoding]::UTF8)
    try { return $reader.ReadToEnd() } finally { $reader.Close() }
}

function Get-FailureInfo($Exception) {
    # Maps an HttpWebRequest failure to Kind = offline | timeout | http | error.
    # Windows PowerShell 5.1 (.NET Framework) reports it in WebException.Status
    # (ConnectFailure / Timeout / ProtocolError); pwsh 7 (.NET) often says UnknownError
    # and keeps the real cause (SocketException, TaskCanceledException) inside.
    $info = [pscustomobject]@{ Kind = "error"; Reason = ""; Response = $null }
    $dropped = $false
    $innermost = $Exception
    $e = $Exception
    while ($null -ne $e) {
        $innermost = $e
        if ($info.Kind -eq "error") {
            if ($e -is [System.Net.WebException]) {
                $st = [string]$e.Status
                if ($st -eq "ProtocolError" -and $null -ne $e.Response) {
                    $info.Kind = "http"; $info.Response = $e.Response
                } elseif ($st -in "ConnectFailure", "NameResolutionFailure") {
                    $info.Kind = "offline"; $info.Reason = Get-LocalizedText 'connection refused'
                } elseif ($st -eq "Timeout") {
                    $info.Kind = "timeout"
                } elseif ($st -in "ConnectionClosed", "ReceiveFailure", "SendFailure", "KeepAliveFailure", "PipelineFailure") {
                    $dropped = $true
                }
            } elseif ($e -is [System.Net.Sockets.SocketException]) {
                $code = [string]$e.SocketErrorCode
                if ($code -in "ConnectionRefused", "HostUnreachable", "NetworkUnreachable", "NetworkDown", "HostDown", "HostNotFound", "AddressNotAvailable") {
                    $info.Kind = "offline"; $info.Reason = Get-LocalizedText 'connection refused'
                } elseif ($code -eq "TimedOut") {
                    $info.Kind = "timeout"
                } else {
                    $dropped = $true  # reset / aborted: the connection broke
                }
            } elseif ($e -is [System.TimeoutException] -or $e -is [System.OperationCanceledException]) {
                $info.Kind = "timeout"
            } elseif ($e -is [System.IO.IOException]) {
                $dropped = $true
            }
        }
        $e = $e.InnerException
    }
    if ($info.Kind -eq "error" -and $dropped) {
        $info.Kind = "offline"; $info.Reason = Get-LocalizedText 'connection closed before the daemon answered'
    }
    if ($info.Kind -eq "error") { $info.Reason = $innermost.Message }
    return $info
}

function Invoke-Daemon {
    # One HTTP call to the daemon; never throws. Returns an object with
    #   Ok, Kind (ok | offline | timeout | http | error), Status (HTTP code),
    #   Data (parsed JSON) and Detail (why it failed).
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [ValidateSet("GET", "POST")][string]$Method = "GET",
        [string]$Body = "",
        [int]$TimeoutMs = $RequestTimeoutMs,
        [switch]$ProbeFirst,  # check the port first: fast "offline" instead of a ~2 s refusal
        [switch]$Quiet        # update reachability without logging the transition
    )
    $out = [pscustomobject]@{ Ok = $false; Kind = "error"; Status = 0; Data = $null; Detail = "" }
    if ($ProbeFirst -and -not (Test-DaemonPort)) {
        $out.Kind = "offline"
        $out.Detail = (Get-LocalizedText 'nothing is listening on {0}') -f $DaemonEndpoint.Authority
        Set-DaemonReachable $false -Quiet:$Quiet
        return $out
    }
    $resp = $null
    try {
        $req = [System.Net.WebRequest]::Create("$DaemonUrl$Path")
        $req.Method = $Method
        $req.Proxy = $null  # no WPAD / system proxy lookup for a loopback call
        $req.Timeout = $TimeoutMs
        $req.ReadWriteTimeout = $TimeoutMs
        $req.KeepAlive = $false
        $req.Accept = "application/json"
        if ($Method -eq "POST") {
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($Body)
            $req.ContentType = "application/json"
            $req.ContentLength = $bytes.Length
            if ($bytes.Length -gt 0) {
                $stream = $req.GetRequestStream()
                try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Close() }
            }
        }
        $resp = $req.GetResponse()
        $out.Status = [int]$resp.StatusCode
        $text = Read-HttpBody $resp
        if ($text) { $out.Data = $text | ConvertFrom-Json }
        $out.Ok = $true
        $out.Kind = "ok"
    } catch {
        $info = Get-FailureInfo $_.Exception
        $out.Kind = $info.Kind
        $out.Detail = $info.Reason
        if ($info.Kind -eq "http") {
            try { $out.Status = [int]$info.Response.StatusCode } catch {}
            $out.Detail = "HTTP $($out.Status)"
            try {
                $err = (Read-HttpBody $info.Response) | ConvertFrom-Json
                if ($err.error) { $out.Detail = "HTTP $($out.Status): $($err.error)" }
            } catch {}
            try { $info.Response.Close() } catch {}
        }
    } finally {
        if ($null -ne $resp) { $resp.Close() }
    }
    # A timeout can hide a refusal (both take ~2 s on Windows loopback): look again.
    if ($out.Kind -eq "timeout" -and -not (Test-DaemonPort)) {
        $out.Kind = "offline"
        $out.Detail = (Get-LocalizedText 'nothing is listening on {0}') -f $DaemonEndpoint.Authority
    }
    Set-DaemonReachable ($out.Kind -ne "offline") -Quiet:$Quiet
    return $out
}

# --- Consumer registration (client_id) ------------------------------------------

function Update-Registration([switch]$Force, [switch]$Quiet) {
    # Lazy: retried every $RegisterRetrySec s while unregistered (daemon not up yet,
    # or it restarted and forgot our id). An old daemon without /register (404) just
    # keeps us unregistered (legacy behavior: global scope).
    if ($script:ClientId) { return }
    $now = Get-Date
    if (-not $Force -and $now -lt $script:NextRegisterAt) { return }
    $script:NextRegisterAt = $now.AddSeconds($RegisterRetrySec)
    $res = Invoke-Daemon -Method POST -Path "/register" -ProbeFirst -Quiet:$Quiet
    if ($res.Ok -and $null -ne $res.Data -and $res.Data.client_id) {
        $script:ClientId = [string]$res.Data.client_id
        if (-not $Quiet) { Write-Host ((Get-LocalizedText '[VoiceMate] Registered with the daemon as consumer {0}.') -f $script:ClientId) }
    }
}

function Sync-DaemonInstance($Instance) {
    # Returns $true when the daemon restarted (new instance): the drain cursor goes
    # back to 0 so the new daemon's results are not skipped, and we register again
    # (client ids live in daemon memory). Before the first baseline there is nothing
    # to reset: the baseline records the instance itself.
    $inst = [string]$Instance
    if ($inst -eq "" -or $null -eq $script:LastSeq -or $inst -ceq $script:DaemonInstance) { return $false }
    $old = $script:DaemonInstance
    if (-not $old) { $old = "?" }
    $script:DaemonInstance = $inst
    $script:LastSeq = 0
    # The new daemon probes its audio again: if it is still down, say so again.
    if ($script:AudioState -eq "down") { $script:AudioState = "recheck" }
    Write-Host ((Get-LocalizedText '[VoiceMate] Daemon restarted (instance {0} -> {1}): reading its results from the start.') -f $old, $inst)
    $script:ClientId = $null
    Update-Registration -Force
    return $true
}

# --- Trigger --------------------------------------------------------------------

function Send-Trigger([string]$Flow, [string]$Key) {
    # Back from an outage: learn the daemon instance first, so a restart drops the
    # stale client_id BEFORE this trigger tags its operation with it (with -Scope
    # mine, a result tagged with the old id would never be delivered).
    if ($script:DaemonOnline -ne $true) { Invoke-HealthCheck -ProbeFirst }
    if ($script:DaemonOnline -eq $false -and $null -ne $script:LastHealthResult -and $script:LastHealthResult.Kind -eq "offline") {
        # The health check just failed to reach the daemon: reuse its verdict and its
        # detail (refused vs. closed mid-request) instead of probing a second time.
        $res = $script:LastHealthResult
    } else {
        $body = @{ flow = $Flow; client_id = $script:ClientId } | ConvertTo-Json -Compress
        $res = Invoke-Daemon -Method POST -Path "/trigger" -Body $body -TimeoutMs $TriggerTimeoutMs -ProbeFirst -Quiet
    }
    if ($res.Ok) {
        $r = $res.Data
        if (-not $script:ClientId -and $null -ne $r -and $r.client_id) {
            $script:ClientId = [string]$r.client_id
            Write-Host ((Get-LocalizedText '[VoiceMate] Registered with the daemon as consumer {0} (assigned on the trigger).') -f $script:ClientId)
        }
        switch ($r.action) {
            "started"   { Write-Host ((Get-LocalizedText '[VoiceMate] > recording... (op {0})') -f $r.op_seq) }
            "stopped"   { Write-Host ((Get-LocalizedText '[VoiceMate] # transcribing... (op {0})') -f $r.op_seq) }
            "restarted" { Write-Host ((Get-LocalizedText '[VoiceMate] ~ restarted (op {0})') -f $r.op_seq) }
            default     { Write-Host ((Get-LocalizedText '[VoiceMate] trigger ''{0}'' received.') -f $Flow) }
        }
        return [string]$r.action
    }
    switch ($res.Kind) {
        "offline" {
            Write-Alert (Get-LocalizedText 'daemon offline') ((Get-LocalizedText '{0}: daemon offline at {1} ({2}). Start it in WSL with `make run`, then press the hotkey again.') -f $Key, $DaemonUrl, $res.Detail)
        }
        "timeout" {
            $sec = [math]::Round($TriggerTimeoutMs / 1000)
            Write-Alert (Get-LocalizedText 'daemon busy') ((Get-LocalizedText '{0}: the daemon did not answer within {1} s (busy). It is running, so it may still act on this key press: check the WSL console before pressing again.') -f $Key, $sec)
        }
        "http" {
            if ($res.Status -ge 500) {
                Write-Alert (Get-LocalizedText 'daemon error') ((Get-LocalizedText '{0}: daemon error ({1}). See the WSL console for details.') -f $Key, $res.Detail)
            } else {
                Write-Alert (Get-LocalizedText 'trigger rejected') ((Get-LocalizedText '{0}: the daemon rejected the trigger ({1}).') -f $Key, $res.Detail)
            }
        }
        default {
            Write-Alert (Get-LocalizedText 'trigger failed') ((Get-LocalizedText '{0}: unexpected error talking to the daemon at {1}: {2}') -f $Key, $DaemonUrl, $res.Detail)
        }
    }
    return $null
}

# --- Results ----------------------------------------------------------------------

function Get-Query {
    $q = "scope=$Scope"
    if ($script:ClientId) { $q = "client_id=$([Uri]::EscapeDataString($script:ClientId))&$q" }
    return $q
}

function Get-Result($Since = $null) {
    # With $Since, the daemon returns the NEXT unseen result (seq > since),
    # so we can drain in order without missing the intermediate ones.
    $path = "/result?$(Get-Query)"
    if ($null -ne $Since) { $path = "$path&since=$Since" }
    return Invoke-Daemon -Path $path
}

function Show-ErrorEvent($r) {
    # An error event (e.g. the daemon could not open the microphone): the clipboard is
    # left alone; tell the user what happened and what to do about it.
    $code = [string]$r.error
    $message = [string]$r.message
    if (-not $message) { $message = (Get-LocalizedText 'The daemon reported an error ({0}).') -f $code }
    Write-Warning ((Get-LocalizedText '[VoiceMate] {0} (seq {1}, op {2}, error ''{3}''; clipboard untouched)') -f $message, $r.seq, $r.op_seq, $code)
    $title = Get-LocalizedText 'error'
    $hint = ""
    if ($code -like "mic*") {
        $title = Get-LocalizedText 'microphone unavailable'
        $mics = Get-ActiveMicCount
        if ($mics -eq 0) {
            $hint = Get-LocalizedText 'No microphone is connected to Windows. Connect one and press the hotkey again.'
        } elseif ($mics -gt 0) {
            $hint = Get-LocalizedText 'Windows has an active microphone, but WSL cannot open it: the WSLg audio bridge is stuck. Run `wsl --shutdown`, then start the daemon again.'
        } else {
            $hint = Get-LocalizedText 'Could not query the Windows audio devices. Check that a microphone is connected; if it is, run `wsl --shutdown` and start the daemon again.'
        }
        Write-Warning "[VoiceMate] $hint"
    }
    $toastText = "$message $hint".Trim()
    Show-Toast "VoiceMate: $title" $toastText
}

function Show-ClipboardFailure($Pending) {
    # Delivery gave up: never lose the text. Toast + the FULL text in the console so
    # the user can copy it by hand.
    $snip = Get-Snippet $Pending.Text
    Write-Warning ((Get-LocalizedText '[VoiceMate] Transcription could not be copied to the clipboard (seq {0}, op {1}) after {2} tries. Full text below, copy it manually:') -f $Pending.Seq, $Pending.OpSeq, ($Pending.Retries + 1))
    Write-Host ((Get-LocalizedText '----- VoiceMate transcription (seq {0}) -----') -f $Pending.Seq)
    Write-Host $Pending.Text
    Write-Host (Get-LocalizedText '----- end -----')
    Show-Toast ("VoiceMate: " + (Get-LocalizedText 'Transcription could not be copied to the clipboard')) ((Get-LocalizedText '"{0}" (the full text is in the VoiceMate console window)') -f $snip)
}

function Send-PendingClip {
    # Retries a text the clipboard refused, at most $ClipboardRetryMax times spaced
    # $ClipboardRetrySec s apart, ONE short attempt per loop pass (the hotkey loop is
    # never blocked for long). Returns $true when nothing is pending anymore.
    $p = $script:PendingClip
    if ($null -eq $p) { return $true }
    if ((Get-Date) -lt $p.NextAt) { return $false }
    $p.Retries++
    if (Set-ClipboardReliable $p.Text -Attempts 1) {
        Write-Host ((Get-LocalizedText '[VoiceMate] clipboard confirmed (seq {0}, op {1}, retry {2}): "{3}"') -f $p.Seq, $p.OpSeq, $p.Retries, (Get-Snippet $p.Text))
        $script:PendingClip = $null
        return $true
    }
    if ($p.Retries -lt $ClipboardRetryMax) {
        $p.NextAt = (Get-Date).AddSeconds($ClipboardRetrySec)
        return $false
    }
    $script:PendingClip = $null
    Show-ClipboardFailure $p
    return $true
}

function Poll-Result {
    # Drains in ORDER: each pass takes the next unseen result (seq > LastSeq) and
    # sets it on the clipboard. This way, recordings made in sequence ALL land in the
    # history (Win+V), in the right order - instead of just the last one (the producer
    # in WSL can emit faster than we poll, and the old single slot lost the intermediate
    # ones).
    if ($null -eq $script:LastSeq) {
        # No baseline yet (script just started, or the daemon was unreachable until
        # now): don't paste whatever was already in the hub.
        $res = Get-Result
        if (-not $res.Ok -or $null -eq $res.Data) { return $false }
        $script:LastSeq = [int64]$res.Data.seq
        $script:DaemonInstance = [string]$res.Data.instance
        return $false
    }
    # A text is still waiting for the clipboard: keep the order, nothing after it yet.
    if ($null -ne $script:PendingClip) { return $false }
    $any = $false
    for ($guard = 0; $guard -lt 500; $guard++) {
        $res = Get-Result $script:LastSeq
        if (-not $res.Ok -or $null -eq $res.Data) { break }
        $r = $res.Data
        if (Sync-DaemonInstance $r.instance) { continue }  # daemon restarted: ask again from 0
        if ($null -eq $r.seq -or [int64]$r.seq -le $script:LastSeq) { break }
        $script:LastSeq = [int64]$r.seq
        $any = $true
        if ($r.error) {
            Show-ErrorEvent $r
        } elseif ($r.text) {
            $snip = Get-Snippet $r.text
            if (Set-ClipboardReliable $r.text) {
                Write-Host ((Get-LocalizedText '[VoiceMate] clipboard confirmed (seq {0}, op {1}): "{2}"') -f $r.seq, $r.op_seq, $snip)
            } else {
                # Keep the text (it is ours now, the cursor moved past it) and retry it
                # from the main loop; the drain resumes only after it is resolved.
                Write-Warning ((Get-LocalizedText '[VoiceMate] could not confirm the clipboard (seq {0}): "{1}"; retrying.') -f $r.seq, $snip)
                $script:PendingClip = [pscustomobject]@{
                    Seq = $r.seq; OpSeq = $r.op_seq; Text = [string]$r.text; Retries = 0
                    NextAt = (Get-Date).AddSeconds($ClipboardRetrySec)
                }
                break
            }
        }
    }
    return $any
}

# --- Health -----------------------------------------------------------------------

function Invoke-HealthCheck([switch]$ProbeFirst) {
    # /health: daemon instance (restart detection) + WSL audio state, warned once per
    # transition. "unknown" (probe not run yet, or no pactl) keeps the last state.
    $script:NextHealthAt = (Get-Date).AddSeconds($HealthIntervalSec)
    $res = Invoke-Daemon -Path "/health" -ProbeFirst:$ProbeFirst
    $script:LastHealthResult = $res
    if (-not $res.Ok -or $null -eq $res.Data) { return }
    $h = $res.Data
    Update-LanguageFromDaemon $h
    [void](Sync-DaemonInstance $h.instance)
    $audio = [string]$h.audio
    if ($audio -eq "down" -and $script:AudioState -ne "down") {
        $script:AudioState = "down"
        $text = Get-LocalizedText 'WSL audio is stuck: the daemon cannot reach the WSLg PulseAudio server, so recording will fail. Run `wsl --shutdown`, then start the daemon again.'
        if ((Get-ActiveMicCount) -eq 0) { $text += " " + (Get-LocalizedText 'Windows has no microphone connected either: connect one first.') }
        Write-Alert (Get-LocalizedText 'WSL audio is down') $text
    } elseif ($audio -eq "ok") {
        if ($script:AudioState -eq "down" -or $script:AudioState -eq "recheck") {
            Write-Host (Get-LocalizedText '[VoiceMate] WSL audio is working again.')
        }
        $script:AudioState = "ok"
    }
}

# --- Startup ------------------------------------------------------------------------

Update-Registration -Force -Quiet
if ($script:DaemonOnline -eq $true) {
    # Speak the daemon's language from the first line on (auto mode only).
    $health = Invoke-Daemon -Path "/health" -Quiet
    if ($health.Ok) { Update-LanguageFromDaemon $health.Data }
}
Write-Host ((Get-LocalizedText '[VoiceMate] Hotkeys active: {0} (clipboard) / {1} (Claude) -> {2}') -f "Ctrl+Alt+V", "Ctrl+Alt+A", $DaemonUrl)
if ($script:ClientId) { $idLabel = $script:ClientId } else { $idLabel = (Get-LocalizedText '(unregistered, retrying every {0} s)') -f $RegisterRetrySec }
Write-Host ((Get-LocalizedText '[VoiceMate] Consumer {0} | scope={1}. Clipboard set by Windows. Leave this window open.') -f $idLabel, $Scope)
if ($script:DaemonOnline -ne $true) {
    Write-Warning ((Get-LocalizedText '[VoiceMate] Daemon not reachable at {0} yet: start it in WSL (`make run`). The hotkeys stay active and this window connects as soon as it is up.') -f $DaemonUrl)
}
if ((Get-ActiveMicCount) -eq 0) {
    Write-Warning (Get-LocalizedText '[VoiceMate] Windows has no active microphone: recording will fail until you connect one.')
}

$hotkeyMods = $MOD_CONTROL -bor $MOD_ALT -bor $MOD_NOREPEAT
if (-not [VoiceMateHotkeys]::RegisterHotKey([IntPtr]::Zero, 1, $hotkeyMods, [uint32][char]'V')) {
    Write-Warning ((Get-LocalizedText '{0} is already registered by another app.') -f "Ctrl+Alt+V")
}
if (-not [VoiceMateHotkeys]::RegisterHotKey([IntPtr]::Zero, 2, $hotkeyMods, [uint32][char]'A')) {
    Write-Warning ((Get-LocalizedText '{0} is already registered by another app.') -f "Ctrl+Alt+A")
}

# Baseline: don't paste whatever was already in the hub at the moment the script
# started (if the daemon is not up yet, this happens on the first contact).
if ($script:DaemonOnline -eq $true) { [void](Poll-Result) }

# When we see "stopped"/"restarted", the daemon is about to transcribe: for a few
# seconds we poll faster (active double-check) until the result arrives.
$fastUntil = [DateTime]::MinValue

$msg = New-Object VoiceMateHotkeys+MSG
$stale = New-Object VoiceMateHotkeys+MSG
$lastLoopError = ""
$lastLoopErrorAt = [DateTime]::MinValue
try {
    while ($true) {
        try {
            # 1) process pending hotkeys (non-blocking)
            while ([VoiceMateHotkeys]::PeekMessage([ref]$msg, [IntPtr]::Zero, 0, 0, $PM_REMOVE)) {
                if ($msg.message -eq $WM_HOTKEY) {
                    $action = switch ([int]$msg.wParam) {
                        1 { Send-Trigger "clipboard" "Ctrl+Alt+V" }
                        2 { Send-Trigger "claude_chat" "Ctrl+Alt+A" }
                    }
                    if ($action -eq "stopped" -or $action -eq "restarted") {
                        $fastUntil = (Get-Date).AddSeconds(20)  # transcribing: active double-check
                    } elseif ($null -eq $action) {
                        # The daemon did not take this press: drop the presses queued while
                        # we waited, or they would start/stop recordings nobody wants anymore.
                        while ([VoiceMateHotkeys]::PeekMessage([ref]$stale, [IntPtr]::Zero, $WM_HOTKEY, $WM_HOTKEY, $PM_REMOVE)) {}
                    }
                }
            }
            # 2) daemon unreachable: only a cheap probe (+ /health) every few seconds, so
            #    the hotkeys stay responsive instead of waiting on ~2 s refusals.
            $now = Get-Date
            if ($script:DaemonOnline -ne $true -and $now -ge $script:NextReconnectAt) {
                $script:NextReconnectAt = $now.AddSeconds($ReconnectIntervalSec)
                Invoke-HealthCheck -ProbeFirst
            }
            # 3) a text the clipboard refused earlier: retry it (works even while offline).
            if ($null -ne $script:PendingClip) { [void](Send-PendingClip) }
            # 4) connected: registration, health watch, new transcriptions -> clipboard.
            if ($script:DaemonOnline -eq $true) {
                Update-Registration
                if ((Get-Date) -ge $script:NextHealthAt) { Invoke-HealthCheck }
                [void](Poll-Result)
            }
        } catch {
            # One bad pass must never end the hotkeys (the window is often hidden).
            # Throttled: the same error at most once a minute.
            $err = $_.Exception.Message
            if ($err -ne $lastLoopError -or ((Get-Date) - $lastLoopErrorAt).TotalSeconds -ge 60) {
                Write-Warning ((Get-LocalizedText '[VoiceMate] Unexpected error in the main loop (continuing): {0}') -f $err)
                $lastLoopError = $err
                $lastLoopErrorAt = Get-Date
            }
            Start-Sleep -Seconds 1
        }
        # Poll fast while waiting for a just-triggered result; otherwise, take it easy.
        if ((Get-Date) -lt $fastUntil) { Start-Sleep -Milliseconds 150 }
        else { Start-Sleep -Milliseconds 300 }
    }
} finally {
    [void][VoiceMateHotkeys]::UnregisterHotKey([IntPtr]::Zero, 1)
    [void][VoiceMateHotkeys]::UnregisterHotKey([IntPtr]::Zero, 2)
}
