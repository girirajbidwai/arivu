"""
Sentence-boundary splitter for streaming TTS.

When the Brain returns a multi-sentence verify phrase, we don't want to
wait for the full text before starting TTS. This module splits on sentence
boundaries so each sentence can be sent to TTS independently, reducing
time-to-first-byte.

Supported sentence-ending punctuation:
  . ? ! । | (Devanagari purna viram, Kannada)
"""

from __future__ import annotations

import re
from typing import Iterator


# Sentence-splitting regex: split on . ? ! । | followed by whitespace or end.
# Keeps the delimiter attached to the preceding text.
_SENTENCE_RE = re.compile(r'(?<=[.?!।|])\s+')


def split_sentences(text: str) -> list[str]:
    """
    Split text into sentences at natural boundaries.

    Returns a list of non-empty strings. If the text has no sentence-ending
    punctuation, returns the full text as a single element.

    >>> split_sentences("ನಿಮ್ಮ ಸಮಸ್ಯೆ ಅರ್ಥವಾಗಿದೆ. ನಿಮ್ಮನ್ನು ಅಧಿಕಾರಿಯೊಂದಿಗೆ ಸಂಪರ್ಕಿಸುತ್ತೇನೆ.")
    ['ನಿಮ್ಮ ಸಮಸ್ಯೆ ಅರ್ಥವಾಗಿದೆ.', 'ನಿಮ್ಮನ್ನು ಅಧಿಕಾರಿಯೊಂದಿಗೆ ಸಂಪರ್ಕಿಸುತ್ತೇನೆ.']

    >>> split_sentences("Are you okay?")
    ['Are you okay?']
    """
    if not text or not text.strip():
        return []

    parts = _SENTENCE_RE.split(text.strip())
    return [p.strip() for p in parts if p.strip()]


def iter_sentences(text: str) -> Iterator[str]:
    """
    Yield sentences one by one. Same as split_sentences but as a generator
    for streaming pipelines.
    """
    yield from split_sentences(text)
