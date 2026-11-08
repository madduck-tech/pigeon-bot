"""Weekly chronicle text: cleaning of the model reply and its block in the Monday post (ADR-0014).

The model reply is untrusted. It is cleaned once, before it is stored, and escaped when the post
is built, so nothing it says can become markup, a link or a mention.
"""

from __future__ import annotations

import re
from html import escape

MAX_CHARS = 400  # hard limit after cleaning; a longer reply is dropped whole
MAX_SENTENCES = 3
NAME_CHARS = 32  # a participant name as it goes to the model

TITLE = "📜 <b>Хроника недели</b>"

_URL = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]*://|www\.|(?:mailto|javascript|tel|tg):)\S*")
# Domain-like words such as example.com or t.me/x: Telegram would turn them into links.
# Only real Cyrillic TLDs count: "Вася.Петя" or "улетели.Вася" is text, not a domain.
_CYRILLIC_TLDS = "рф|рус|онлайн|сайт|орг|ком|москва|дети|бел|укр|қаз|срб|мкд|бг"
_DOMAIN = re.compile(
    rf"(?i)\b(?:[\w-]+\.)+(?:[a-z]{{2,24}}|(?:{_CYRILLIC_TLDS})(?![\w]))\b(?:/\S*)?"
)
_MARKUP = re.compile(r"[*`#<>@＠﹫]")
_SPACES = re.compile(r"\s+")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.!?…:;])")
_SENTENCE = re.compile(r"[^.!?…]+(?:[.!?…]+[»\")]*|$)")
_KEEP = {"\u200d"}  # zero-width joiner keeps composite emoji whole


def _printable(text: str) -> str:
    """Control and format characters (newlines, bidi overrides, zero-width marks) become spaces."""
    return "".join(ch if ch.isprintable() or ch in _KEEP else " " for ch in text)


def clean(raw: str | None) -> str | None:
    """The chronicle as plain text, or None if the reply is empty or too long."""
    if not isinstance(raw, str):
        return None
    text = _printable(raw)
    text = _URL.sub(" ", text)
    text = _DOMAIN.sub(" ", text)
    text = _MARKUP.sub("", text)
    text = _SPACES.sub(" ", text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text).strip(" \"'«»")
    ends = [m.end() for m in _SENTENCE.finditer(text) if m.group().strip()]
    if len(ends) > MAX_SENTENCES:
        text = text[: ends[MAX_SENTENCES - 1]]
    text = text.strip()
    if not any(ch.isalpha() for ch in text) or len(text) > MAX_CHARS:
        return None
    return text


def clean_name(name: str) -> str:
    """A participant name for the model: printable, single-spaced and short. Data, not markup."""
    text = _SPACES.sub(" ", _printable(name)).strip()
    return text[:NAME_CHARS] or "?"


def block(text: str) -> str:
    return f"{TITLE}\n<i>{escape(text, quote=False)}</i>"
