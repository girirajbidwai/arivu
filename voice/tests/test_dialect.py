"""
Dialect classifier — sanity checks against representative utterances.

The marker set is small for the prototype; tests confirm each dialect
fires on its own discriminative markers and that we return `unknown`
for empty / generic input.
"""

from voice.brain.dialect import DialectClassifier


def _c() -> DialectClassifier:
    return DialectClassifier()


def test_dharwad_marker_fires():
    r = _c().classify("ನೀವು ಎನ್ನಾ ಮಾಡ್ಬೇಕ ರೀ")
    assert r.dialect == "dharwad"
    assert r.confidence > 0.2
    assert "ರೀ" in r.markers_matched


def test_mangaluru_marker_fires():
    r = _c().classify("ಬುಲ್ಲಿ ಬೇಡಾ ಎನ್ರಾ")
    assert r.dialect == "mangaluru"


def test_mysuru_marker_fires():
    r = _c().classify("ಅವರು ಮಾಡುತ್ತಾರೆ ಧನ್ಯವಾದಗಳು")
    assert r.dialect == "mysuru"


def test_bengaluru_codemix_marker_fires():
    r = _c().classify("ನಾನು ಆಪ್ಸ್ ಗೆ ಹೋಗ್ತಿದೀನಿ actually")
    assert r.dialect == "bengaluru"


def test_unknown_on_empty():
    r = _c().classify("")
    assert r.dialect == "unknown"
    assert r.confidence == 0.0


def test_unknown_on_generic_text():
    r = _c().classify("ಸರಿ")  # below floor for any dialect
    assert r.dialect == "unknown"


def test_confidence_capped_at_one():
    # Pile up multiple Dharwad markers — confidence still <= 1.
    text = "ರೀ ಹೋಗ್ತಿ ಬರಾಕ ಮಾಡ್ಬೇಕ ಎನ್ನಾ ತೊಂದರ್"
    r = _c().classify(text)
    assert r.dialect == "dharwad"
    assert 0.0 <= r.confidence <= 1.0
