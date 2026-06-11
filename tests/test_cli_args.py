from __future__ import annotations

from app.cli.args import parse_args
from app.cli.config_builder import build_config


def test_parse_args_no_flags_uses_defaults() -> None:
    args = parse_args([])
    assert args.model == "large-v3-turbo"
    assert args.hotkey == "ctrl+alt+v"
    assert args.claude_chat_hotkey == "ctrl+alt+a"
    assert args.claude_model == "claude-haiku-4-5"
    assert args.claude_effort == "low"
    assert args.claude_enable_thinking is False
    assert args.no_claude_chat is False
    assert args.no_tts is False
    assert args.tts_voice_seed_mode == "off"
    assert args.output_lang == "pt-BR"


def test_parse_args_output_lang_override() -> None:
    args = parse_args(["--output-lang", "en"])
    assert args.output_lang == "en"


def test_parse_args_claude_no_system_prompt() -> None:
    args = parse_args(["--claude-no-system-prompt"])
    assert args.claude_no_system_prompt is True
    assert args.claude_system_prompt is None


def test_build_config_propagates_output_lang() -> None:
    args = parse_args(["--output-lang", "en"])
    config = build_config(args)
    assert config.output_lang == "en"


def test_build_config_default_system_prompt_is_none() -> None:
    """Default system_prompt should be None (= use canonical with output_lang)."""
    args = parse_args([])
    config = build_config(args)
    assert config.claude_chat.system_prompt is None


def test_build_config_no_system_prompt_marks_empty_sentinel() -> None:
    args = parse_args(["--claude-no-system-prompt"])
    config = build_config(args)
    assert config.claude_chat.system_prompt == ""


def test_build_config_custom_system_prompt_passes_through() -> None:
    args = parse_args(["--claude-system-prompt", "Be concise."])
    config = build_config(args)
    assert config.claude_chat.system_prompt == "Be concise."
