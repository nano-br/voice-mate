"""Assistant reply parser: template blocks, degraded replies, actions, expression tags."""

from __future__ import annotations

import random
import string

import pytest

from app.protocol.assistant_reply import Action, parse_reply, strip_expressions

FULL = """<voice>
Você tem duas conversas abertas. [pause] Quer que eu leia a última?
</voice>

<display>
- **voicemate**: testes
- **docs**: revisão
</display>

<actions>
{"version": 1, "actions": [{"type": "notify", "text": "Pronto"}]}
</actions>"""

EMPTY_ACTIONS = '<actions>{"version": 1, "actions": []}</actions>'


def test_full_template() -> None:
    reply = parse_reply(FULL)
    assert reply.voice == "Você tem duas conversas abertas. Quer que eu leia a última?"
    assert "[pause]" in reply.voice_raw
    assert reply.display.startswith("- **voicemate**")
    assert reply.actions == (Action(type="notify", params={"text": "Pronto"}),)
    assert not reply.degraded
    assert reply.problems == ()


def test_missing_display() -> None:
    reply = parse_reply(f"<voice>Done.</voice>{EMPTY_ACTIONS}")
    assert reply.voice == "Done."
    assert reply.display == ""
    assert reply.actions == ()
    assert reply.problems == ()


def test_missing_voice_is_degraded() -> None:
    reply = parse_reply("  Just typed text in the Desktop.  ")
    assert reply.degraded
    assert reply.voice == ""
    assert reply.voice_raw == ""
    assert reply.display == "Just typed text in the Desktop."
    assert reply.actions == ()
    assert reply.problems


def test_missing_actions_block_is_a_problem_but_not_degraded() -> None:
    reply = parse_reply("<voice>Hello</voice>")
    assert not reply.degraded
    assert reply.voice == "Hello"
    assert reply.problems == ("no <actions> block",)


def test_case_and_whitespace_tolerant() -> None:
    reply = parse_reply('<VOICE >\n  Hi there  \n</Voice>\n<Actions>{"version":1,"actions":[]}</ACTIONS >')
    assert reply.voice == "Hi there"
    assert reply.problems == ()


@pytest.mark.parametrize("cut", ["</actions", "</acti", "</", ""])
def test_truncated_final_tag(cut: str) -> None:
    reply = parse_reply('<voice>Ok</voice><actions>{"version": 1, "actions": []}' + cut)
    assert reply.voice == "Ok"
    assert reply.actions == ()
    assert reply.problems == ()


def test_truncated_voice_at_the_end() -> None:
    reply = parse_reply("<voice>Half a sentence")
    assert reply.voice == "Half a sentence"
    assert not reply.degraded


def test_bad_json() -> None:
    reply = parse_reply("<voice>Hi</voice><actions>{not json</actions>")
    assert reply.voice == "Hi"
    assert reply.actions == ()
    assert reply.problems == ("actions is not valid JSON",)


def test_actions_json_not_an_object() -> None:
    reply = parse_reply("<voice>Hi</voice><actions>[1, 2]</actions>")
    assert reply.actions == ()
    assert len(reply.problems) == 1


@pytest.mark.parametrize("version", ["2", "0", '"1"', "true", "null"])
def test_unknown_version(version: str) -> None:
    body = '{"version": ' + version + ', "actions": [{"type": "notify", "text": "x"}]}'
    reply = parse_reply(f"<voice>Hi</voice><actions>{body}</actions>")
    assert reply.actions == ()
    assert len(reply.problems) == 1
    assert "version" in reply.problems[0]


def test_unknown_action_type_is_dropped_and_logged() -> None:
    body = (
        '{"version": 1, "actions": [{"type": "launch_missiles"}, '
        '{"type": "follow_up", "session_id": "local_1", "reason": "waiting"}, '
        '{"no_type": 1}, 5]}'
    )
    reply = parse_reply(f"<voice>Hi</voice><actions>{body}</actions>")
    assert [a.type for a in reply.actions] == ["follow_up"]
    assert reply.actions[0].params == {"session_id": "local_1", "reason": "waiting"}
    assert len(reply.problems) == 3
    assert any("launch_missiles" in p for p in reply.problems)


def test_actions_without_list() -> None:
    reply = parse_reply('<voice>Hi</voice><actions>{"version": 1}</actions>')
    assert reply.actions == ()
    assert reply.problems == ("actions has no list",)


def test_first_block_wins_when_repeated() -> None:
    assert parse_reply("<voice>one</voice><voice>two</voice>").voice == "one"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ha [laugh] ok", "Ha ok"),
        ("Well [laugh], fine.", "Well, fine."),
        ("[whisper]secret[/whisper] now", "secret now"),
        ("A [chuckle] B [sigh] C [pause] D", "A B C D"),
        ("Odd [giggle] tag", "Odd tag"),
        ("Keep [see note 2] and [3] and [a b].", "Keep [see note 2] and [3] and [a b]."),
        ("Tag [ñandú] here", "Tag here"),
        ("  lots \n of   space ", "lots of space"),
        ("", ""),
    ],
)
def test_strip_expressions(raw: str, expected: str) -> None:
    assert strip_expressions(raw) == expected


@pytest.mark.parametrize(
    ("voice", "spoken"),
    [
        ("Tudo certo [laugh], já terminei a revisão.", "Tudo certo, já terminei a revisão."),
        ("All done [sigh] finally, the tests pass.", "All done finally, the tests pass."),
        ("Listo [pause] ¿quieres que lo lea?", "Listo ¿quieres que lo lea?"),
    ],
)
def test_languages(voice: str, spoken: str) -> None:
    reply = parse_reply(f"<voice>{voice}</voice>{EMPTY_ACTIONS}")
    assert reply.voice == spoken
    assert reply.voice_raw == voice


def test_fuzz_never_raises() -> None:
    rng = random.Random(1234)
    pieces = [
        "<voice>",
        "</voice>",
        "<display>",
        "</display>",
        "<actions>",
        "</actions>",
        "{",
        "}",
        '{"version": 1, "actions": [',
        '{"type": "notify"}',
        "]}",
        "[laugh]",
        "[",
        "]",
        "</",
        "<",
        "\n",
        " ",
        "é",
        "\x00",
        "9" * 5000,
        "[" * 3000,
    ]
    alphabet = string.printable + "áéíõñç"
    for _ in range(400):
        if rng.random() < 0.5:
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 80)))
        else:
            text = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 12)))
        reply = parse_reply(text)
        assert isinstance(reply.voice, str)
        assert isinstance(reply.problems, tuple)
        if reply.degraded:
            assert reply.voice == ""
