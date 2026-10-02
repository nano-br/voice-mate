; VoiceMate - global Windows hotkeys -> daemon in WSL2.
;
; Requires AutoHotkey v2 (https://www.autohotkey.com). Double-click to activate;
; to start with Windows, place a shortcut to this file in shell:startup.
;
; The daemon (running inside WSL with `make run`, trigger=socket) listens on
; 127.0.0.1:47821 - WSL2's localhostForwarding exposes the port to Windows.
; Each hotkey is equivalent to pressing the shortcut for that flow: "stop decides
; the destination".

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
        TrayTip("VoiceMate", "Daemon offline at " . DaemonUrl . " - run 'make run' in WSL.", 2)
    }
}

^!v:: Trigger("clipboard")    ; Ctrl+Alt+V - voice -> clipboard
^!a:: Trigger("claude_chat")  ; Ctrl+Alt+A - voice -> Claude -> TTS
