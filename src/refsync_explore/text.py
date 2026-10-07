"""Small text helpers for ADS records."""

import html
import re

from refsync.services.bibtex import split_name

_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")


def clean_ads_text(text):
    """ADS titles/abstracts carry HTML entities and tags (<SUB>, <SUP>, <BR />)."""
    if not text:
        return text
    text = html.unescape(_TAG_RE.sub("", text))
    return re.sub(r"\s+", " ", text).strip()


def display_name(name: str) -> str:
    """'van Dokkum, Pieter G.' -> 'Pieter G. van Dokkum' (groups stay as-is)."""
    first, last, suffix = split_name(name)
    if not last:
        return name.strip()
    out = f"{first} {last}".strip()
    return f"{out}, {suffix}" if suffix else out


def surname(name: str) -> str:
    """Display surname with particles and accents: 'van Dokkum', 'Kereš'."""
    _, last, _ = split_name(name)
    return last or name.strip()


def author_label(authors: list[str], author_count=None) -> str:
    """'Chang', 'Chang & Lan', or 'Chang et al.'"""
    if not authors:
        return "Unknown"
    n = max(author_count or 0, len(authors))
    if n == 1:
        return surname(authors[0])
    if n == 2 and len(authors) >= 2:
        return f"{surname(authors[0])} & {surname(authors[1])}"
    return f"{surname(authors[0])} et al."
