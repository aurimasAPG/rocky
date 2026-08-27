from rocky.jsonutil import parse_json_object


def test_parse_raw_object():
    assert parse_json_object('{"a": 1}') == {"a": 1}


def test_parse_fenced_json():
    text = """Here you go
```json
{"goal": "x", "complexity": "hard"}
```
thanks"""
    assert parse_json_object(text)["complexity"] == "hard"


def test_parse_embedded_object():
    text = 'prefix {"passed": true, "score": 0.9} suffix'
    assert parse_json_object(text)["passed"] is True
