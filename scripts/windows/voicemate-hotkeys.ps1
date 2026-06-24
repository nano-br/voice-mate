# VoiceMate - hotkeys globais do Windows + clipboard nativo -> daemon no WSL2.
#
# Uso:   powershell -ExecutionPolicy Bypass -File voicemate-hotkeys.ps1
# Dica:  para rodar oculto no login, crie um atalho em shell:startup com
#        powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File <caminho>\voicemate-hotkeys.ps1
#
# Faz TRES coisas:
#  1) Registra-se no daemon (POST /register) e recebe um client_id. Assim este
#     consumidor se identifica - varios ouvintes podem coexistir.
#  2) Registra Ctrl+Alt+V (clipboard) e Ctrl+Alt+A (Claude) via RegisterHotKey
#     Win32 e dispara POST /trigger. A resposta diz a ACAO (started/stopped/
#     restarted) - feedback imediato de que o atalho foi recebido e o que fez.
#  3) Faz polling de GET /result e seta o clipboard do Windows com Set-Clipboard.
#     No WSL2 a ponte de clipboard do WSLg/interop e instavel, entao quem escreve
#     o clipboard de verdade e o Windows (nativo, confiavel). Apos um "stopped"
#     (transcrevendo), faz double-check ativo e busca o resultado com retry,
#     porque o fetch do clipboard pode falhar.
#
# -Scope "all" (default): ouve o resultado de QUALQUER consumidor (config mais
#   generica). -Scope "mine": ouve so o que ESTE client_id iniciou.

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
    # Tolerante a um \r\n sobrando que o clipboard as vezes normaliza.
    return ($A -ceq $B) -or ($A.TrimEnd("`r", "`n") -ceq $B.TrimEnd("`r", "`n"))
}

function Set-ClipboardReliable([string]$Text) {
    # Entrega confiavel em DUAS etapas. Devolve $true se o texto ficou no clipboard.
    #
    # 1) GARANTE o clipboard atual: o Set-Clipboard as vezes "sucede" sem aplicar
    #    (clipboard travado por outro processo por um instante). So confiamos no
    #    READ-BACK; repetimos com backoff curto ate confirmar.
    $confirmed = $false
    for ($i = 1; $i -le 5; $i++) {
        try { Set-Clipboard -Value $Text } catch {}
        Start-Sleep -Milliseconds (60 * $i)  # 60,120,180,240,300
        $cur = ""
        try { $cur = Get-Clipboard -Raw } catch {}
        if (Compare-ClipText $cur $Text) { $confirmed = $true; break }
    }
    if (-not $confirmed) { return $false }

    # 2) HISTORICO (Win+V): o servico coalesce mudancas rapidas, entao um set unico
    #    as vezes passa de raspao e NAO vira entrada (era a intermitencia). Damos um
    #    respiro pro valor estabilizar e RE-ASSERTAMOS — uma segunda escrita do
    #    mesmo valor e nova chance de captura (o "setar duas vezes"). Idempotente:
    #    o Win+V deduplica entradas consecutivas iguais, entao nao polui.
    #    So re-assertamos se o clipboard AINDA for o nosso: se o usuario copiou algo
    #    no respiro, respeitamos e nao sobrescrevemos.
    Start-Sleep -Milliseconds 250
    try { $cur = Get-Clipboard -Raw } catch { $cur = "" }
    if (Compare-ClipText $cur $Text) {
        try { Set-Clipboard -Value $Text } catch {}
        Start-Sleep -Milliseconds 250
    }
    return $true
}

# --- Registro do consumidor (client_id). Se o daemon for antigo (404), seguimos
# --- sem id (comportamento legado: scope=all global).
$ClientId = $null
try {
    $ClientId = (Invoke-RestMethod -Method Post -Uri "$DaemonUrl/register" -TimeoutSec 2).client_id
} catch {}
$idLabel = if ($ClientId) { $ClientId } else { "(sem registro)" }

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
            "started"   { Write-Host "[VoiceMate] > gravando... (op $($r.op_seq))" }
            "stopped"   { Write-Host "[VoiceMate] # transcrevendo... (op $($r.op_seq))" }
            "restarted" { Write-Host "[VoiceMate] ~ reiniciado (op $($r.op_seq))" }
            default     { Write-Host "[VoiceMate] trigger '$Flow' recebido." }
        }
        return $r.action
    } catch {
        Write-Warning "[VoiceMate] Daemon offline em $DaemonUrl (rode 'make run' no WSL)."
        return $null
    }
}

function Get-Result($Since = $null) {
    # Com $Since, o daemon devolve o PROXIMO resultado nao-visto (seq > since),
    # para drenarmos em ordem sem perder os intermediarios.
    $uri = "$DaemonUrl/result?$(Get-Query)"
    if ($null -ne $Since) { $uri = "$uri&since=$Since" }
    try { return Invoke-RestMethod -Uri $uri -TimeoutSec 2 } catch { return $null }
}

function Get-Snippet([string]$Text, [int]$Max = 60) {
    # Trecho de uma linha para auditoria no log (normaliza espacos/quebras).
    if ([string]::IsNullOrEmpty($Text)) { return "" }
    $flat = ($Text -replace '\s+', ' ').Trim()
    if ($flat.Length -gt $Max) { return $flat.Substring(0, $Max) + [char]0x2026 }
    return $flat
}

Write-Host "[VoiceMate] Hotkeys ativas: Ctrl+Alt+V (clipboard) / Ctrl+Alt+A (Claude) -> $DaemonUrl"
Write-Host "[VoiceMate] Consumidor $idLabel | scope=$Scope. Clipboard setado pelo Windows. Deixe esta janela aberta."

if (-not [VoiceMateHotkeys]::RegisterHotKey([IntPtr]::Zero, 1, $MOD_CONTROL -bor $MOD_ALT, [uint32][char]'V')) {
    Write-Warning "Ctrl+Alt+V ja esta registrado por outro app."
}
if (-not [VoiceMateHotkeys]::RegisterHotKey([IntPtr]::Zero, 2, $MOD_CONTROL -bor $MOD_ALT, [uint32][char]'A')) {
    Write-Warning "Ctrl+Alt+A ja esta registrado por outro app."
}

# Nao cola o que ja estava no hub no momento em que o script subiu.
$lastSeq = -1
$r0 = Get-Result
if ($r0) { $lastSeq = $r0.seq }

# Quando vemos "stopped"/"restarted", o daemon vai transcrever: por alguns
# segundos pollamos mais rapido (double-check ativo) ate o resultado chegar.
$fastUntil = [DateTime]::MinValue

function Poll-Result {
    # Drena em ORDEM: cada volta pega o proximo resultado nao-visto (seq > lastSeq)
    # e o seta no clipboard. Assim, gravacoes em sequencia entram TODAS no historico
    # (Win+V), na ordem certa — em vez de so a ultima (o produtor no WSL pode gerar
    # mais rapido do que pollamos, e o slot unico antigo perdia os intermediarios).
    $any = $false
    while ($true) {
        $r = Get-Result $script:lastSeq
        if (-not $r -or $r.seq -le $script:lastSeq) { break }
        $script:lastSeq = $r.seq
        $any = $true
        if ($r.text) {
            $snip = Get-Snippet $r.text
            if (Set-ClipboardReliable $r.text) {
                Write-Host "[VoiceMate] clipboard confirmado (seq $($r.seq), op $($r.op_seq)): `"$snip`""
            } else {
                Write-Warning "[VoiceMate] nao confirmei o clipboard (seq $($r.seq)): `"$snip`" apos varias tentativas."
            }
        }
    }
    return $any
}

$msg = New-Object VoiceMateHotkeys+MSG
try {
    while ($true) {
        # 1) processa hotkeys pendentes (nao-bloqueante)
        while ([VoiceMateHotkeys]::PeekMessage([ref]$msg, [IntPtr]::Zero, 0, 0, $PM_REMOVE)) {
            if ($msg.message -eq $WM_HOTKEY) {
                $action = switch ([int]$msg.wParam) {
                    1 { Send-Trigger "clipboard" }
                    2 { Send-Trigger "claude_chat" }
                }
                if ($action -eq "stopped" -or $action -eq "restarted") {
                    $fastUntil = (Get-Date).AddSeconds(20)  # transcrevendo: double-check ativo
                }
            }
        }
        # 2) ha transcricao/resposta nova? seta o clipboard do Windows.
        [void](Poll-Result)
        # Poll rapido enquanto esperamos um resultado recem-disparado; senao, calmo.
        if ((Get-Date) -lt $fastUntil) { Start-Sleep -Milliseconds 150 }
        else { Start-Sleep -Milliseconds 300 }
    }
} finally {
    [void][VoiceMateHotkeys]::UnregisterHotKey([IntPtr]::Zero, 1)
    [void][VoiceMateHotkeys]::UnregisterHotKey([IntPtr]::Zero, 2)
}
