import argparse
import sys

from app.core.config import ClaudeChatConfig, Config, FlowConfig
from app.services.audio_feedback import AudioFeedback
from app.services.claude_chat_handler import ClaudeChatHandler
from app.services.claude_runtime import ClaudeRuntime
from app.services.input_listener import (
    InputListener,
    KeyboardHotkeyListener,
    MouseButtonListener,
)
from app.services.listener_keepalive import ListenerKeepalive
from app.services.multi_hotkey_listener import MultiHotkeyListener
from app.services.recorder import Recorder
from app.services.recording_session import RecordingSession
from app.services.transcriber import Transcriber
from app.services.transcription_handler import ClipboardHandler, TranscriptionHandler
from app.services.watchdog import Watchdog


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="VoiceMate — voz para clipboard ou Claude")
    parser.add_argument(
        "--model",
        default="large-v3-turbo",
        choices=["tiny", "base", "small", "medium", "large-v2", "large-v3", "large-v3-turbo"],
        help="Modelo Whisper (padrão: large-v3-turbo)",
    )
    parser.add_argument(
        "--hotkey",
        default="ctrl+alt+v",
        help="Hotkey global do fluxo clipboard (padrão: ctrl+alt+v)",
    )
    parser.add_argument(
        "--cpu",
        action="store_true",
        help="Forçar uso de CPU em vez de GPU (usa int8)",
    )
    parser.add_argument(
        "--input-method",
        default="keyboard",
        choices=["keyboard", "mouse"],
        help="Método de input (padrão: keyboard)",
    )
    parser.add_argument(
        "--mouse-button",
        default="x",
        help="Botão do mouse para usar como trigger (padrão: x = botão lateral)",
    )
    parser.add_argument(
        "--max-recording-seconds",
        type=int,
        default=600,
        help="Tempo máximo de gravação em segundos (padrão: 600 = 10min)",
    )
    parser.add_argument(
        "--no-watchdog",
        action="store_true",
        help="Desabilitar watchdog de auto-recovery",
    )
    parser.add_argument(
        "--watchdog-timeout",
        type=int,
        default=120,
        help="Timeout do watchdog em segundos (padrão: 120)",
    )
    parser.add_argument(
        "--no-listener-refresh",
        action="store_true",
        help="Desabilitar reinstalação periódica do listener",
    )
    parser.add_argument(
        "--listener-refresh-seconds",
        type=int,
        default=60,
        help="Intervalo de reinstalação do listener em segundos (padrão: 60)",
    )
    parser.add_argument(
        "--claude-chat-hotkey",
        default="ctrl+alt+a",
        help="Hotkey global do fluxo voz→Claude→clipboard (padrão: ctrl+alt+a)",
    )
    parser.add_argument(
        "--no-claude-chat",
        action="store_true",
        help="Desabilitar fluxo de chat com Claude (só clipboard)",
    )
    parser.add_argument(
        "--claude-system-prompt",
        default=None,
        help="System prompt opcional para o fluxo Claude",
    )
    parser.add_argument(
        "--claude-max-turns",
        type=int,
        default=50,
        help="Máximo de turnos por sessão Claude (padrão: 50)",
    )
    return parser.parse_args()


def _build_config(args: argparse.Namespace) -> Config:
    claude_chat_enabled = not args.no_claude_chat and args.input_method == "keyboard"
    if args.no_claude_chat is False and args.input_method == "mouse":
        print(
            "[VoiceMate] ⚠ Fluxo Claude requer input-method=keyboard. Desabilitando.",
            file=sys.stderr,
        )
    return Config(
        model_size=args.model,
        hotkey=args.hotkey,
        use_cpu=args.cpu,
        input_method=args.input_method,
        mouse_button=args.mouse_button,
        max_recording_seconds=args.max_recording_seconds,
        watchdog_enabled=not args.no_watchdog,
        watchdog_timeout_seconds=args.watchdog_timeout,
        listener_refresh_enabled=not args.no_listener_refresh,
        listener_refresh_seconds=args.listener_refresh_seconds,
        claude_chat_enabled=claude_chat_enabled,
        claude_chat_hotkey=args.claude_chat_hotkey,
        claude_chat=ClaudeChatConfig(
            system_prompt=args.claude_system_prompt,
            max_turns=args.claude_max_turns,
        ),
    )


def _build_handlers(
    flows: list[FlowConfig],
    audio: AudioFeedback,
) -> tuple[dict[str, TranscriptionHandler], list[TranscriptionHandler]]:
    """Constrói handlers para cada fluxo. Retorna (mapa, lista para close)."""
    handlers: dict[str, TranscriptionHandler] = {}
    owned: list[TranscriptionHandler] = []
    for flow in flows:
        if flow.kind == "clipboard":
            handler: TranscriptionHandler = ClipboardHandler(audio)
        elif flow.kind == "claude_chat":
            chat_cfg = flow.claude_chat or ClaudeChatConfig()
            runtime = ClaudeRuntime(
                system_prompt=chat_cfg.system_prompt,
                max_turns=chat_cfg.max_turns,
            )
            try:
                runtime.start()
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[VoiceMate] ⚠ Falha ao iniciar Claude (rode `claude login`): {exc}",
                    file=sys.stderr,
                )
                continue
            handler = ClaudeChatHandler(runtime, audio)
        else:
            print(f"[VoiceMate] ⚠ Fluxo desconhecido ignorado: {flow.kind}", file=sys.stderr)
            continue
        handlers[flow.name] = handler
        owned.append(handler)
    return handlers, owned


class _HotkeyCallback:
    """Wrapper estável (não-closure) para capturar o handler_id por hotkey."""

    def __init__(self, session: RecordingSession, handler_id: str) -> None:
        self._session = session
        self._handler_id = handler_id

    def __call__(self) -> None:
        self._session.toggle(self._handler_id)


def _build_listener(
    config: Config,
    flows: list[FlowConfig],
    session: RecordingSession,
) -> InputListener:
    if config.input_method == "mouse":
        # mouse só suporta um trigger — usa o primeiro fluxo (clipboard por default)
        flow_name = flows[0].name
        callback = _HotkeyCallback(session, flow_name)
        return MouseButtonListener(button=config.mouse_button, on_toggle=callback)  # type: ignore[return-value]

    # keyboard: registra um callback por hotkey distinto
    bindings: dict[str, _HotkeyCallback] = {}
    for flow in flows:
        if flow.hotkey not in bindings:
            bindings[flow.hotkey] = _HotkeyCallback(session, flow.name)
    if len(bindings) == 1:
        # Atalho único — usa KeyboardHotkeyListener simples
        hotkey, cb = next(iter(bindings.items()))
        return KeyboardHotkeyListener(hotkey=hotkey, on_toggle=cb)  # type: ignore[return-value]
    return MultiHotkeyListener({hk: cb for hk, cb in bindings.items()})  # type: ignore[return-value]


def main() -> None:
    args = _parse_args()
    config = _build_config(args)

    recorder = Recorder(sample_rate=config.sample_rate)
    transcriber = Transcriber(
        model_size=config.model_size,
        use_cpu=config.use_cpu,
        beam_size=config.beam_size,
    )
    audio_feedback = AudioFeedback()

    flows = config.flows or config.build_default_flows()
    handlers, owned_handlers = _build_handlers(flows, audio_feedback)
    if not handlers:
        print("[VoiceMate] Nenhum handler disponível, encerrando.", file=sys.stderr)
        sys.exit(1)

    # Reduz fluxos para os que realmente têm handler (no caso de claude falhar)
    flows = [f for f in flows if f.name in handlers]
    default_handler_id = "clipboard" if "clipboard" in handlers else flows[0].name

    session = RecordingSession(
        recorder=recorder,
        transcriber=transcriber,
        audio=audio_feedback,
        config=config,
        handlers=handlers,
        default_handler_id=default_handler_id,
    )
    listener = _build_listener(config, flows, session)

    print(f"\n[VoiceMate] Pronto. Input: {config.input_method}")
    for flow in flows:
        label = "clipboard" if flow.kind == "clipboard" else "Claude (multi-turn)"
        print(f"[VoiceMate] Hotkey {flow.hotkey}: → {label}")
    print(f"[VoiceMate] Tempo máximo de gravação: {config.max_recording_seconds}s")
    print("[VoiceMate] Ctrl+C para sair.\n")

    watchdog: Watchdog | None = None
    if config.watchdog_enabled:
        watchdog = Watchdog(timeout_seconds=config.watchdog_timeout_seconds)
        watchdog.start()

    keepalive: ListenerKeepalive | None = None
    if config.listener_refresh_enabled:
        keepalive = ListenerKeepalive(
            listener,
            interval_seconds=float(config.listener_refresh_seconds),
        )
        keepalive.start()

    try:
        listener.listen()
    except KeyboardInterrupt:
        pass
    finally:
        if keepalive is not None:
            keepalive.stop()
        if watchdog is not None:
            watchdog.stop()
        for handler in owned_handlers:
            try:
                handler.close()
            except Exception as exc:  # noqa: BLE001
                print(f"[VoiceMate] Falha ao fechar handler: {exc}", file=sys.stderr)
        print("\n[VoiceMate] Encerrando.")


if __name__ == "__main__":
    main()
