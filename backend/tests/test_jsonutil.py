import pytest

from brain.jsonutil import extract_json


def test_plain_json():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_fenced_with_think_and_trailing_comma():
    text = '<think>ragiono {"x": 0}</think>\nEcco:\n```json\n{"action": "finish", "args": {"success": true,},}\n```'
    assert extract_json(text)["action"] == "finish"


def test_json_embedded_in_prose_with_braces_in_strings():
    text = 'Certo! {"thought": "uso { e } nel testo", "action": "x"} fine.'
    assert extract_json(text)["action"] == "x"


def test_unterminated_think_block_raises():
    with pytest.raises(ValueError):
        extract_json('<think>non finisco mai {"a": 1}')


def test_no_json_raises():
    with pytest.raises(ValueError):
        extract_json("nessun json qui")
