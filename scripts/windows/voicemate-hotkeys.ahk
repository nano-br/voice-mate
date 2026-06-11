; VoiceMate — hotkeys globais do Windows → daemon no WSL2.
;
; Requer AutoHotkey v2 (https://www.autohotkey.com). Dê dois cliques para ativar;
; para iniciar com o Windows, coloque um atalho deste arquivo em shell:startup.
;
; O daemon (rodando dentro do WSL com `make run`, trigger=socket) escuta em
; 127.0.0.1:47821 — o localhostForwarding do WSL2 expõe a porta ao Windows.
; Cada hotkey equivale a apertar o atalho daquele fluxo: "stop decide o destino".

#Requires AutoHotkey v2.0
#SingleInstance Force

DaemonUrl := "http://127.0.0.1:47821"

Trigger(flow) {
    global DaemonUrl
    try {
        req := ComObject("WinHttp.WinHttpRequest.5.1")
        req.Open("POST", DaemonUrl . "/trigger", false)
        req.SetRequestHeader("Content-Type", "application/json")
        req.SetTimeouts(500, 500, 500, 2000)
        req.Send('{"flow": "' . flow . '"}')
    } catch {
        TrayTip("VoiceMate", "Daemon offline em " . DaemonUrl . " — rode 'make run' no WSL.", 2)
    }
}

^!v:: Trigger("clipboard")    ; Ctrl+Alt+V — voz → clipboard
^!a:: Trigger("claude_chat")  ; Ctrl+Alt+A — voz → Claude → TTS
