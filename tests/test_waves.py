from rocky.waves import schedule_waves


def test_independent_steps_share_one_wave():
    steps = [
        {"id": 1, "title": "research", "depends_on": []},
        {"id": 2, "title": "examples", "depends_on": []},
    ]
    waves = schedule_waves(steps)
    assert len(waves) == 1
    assert {s["id"] for s in waves[0]} == {1, 2}


def test_depends_on_creates_two_waves():
    steps = [
        {"id": 1, "title": "research", "depends_on": []},
        {"id": 2, "title": "write", "depends_on": [1]},
    ]
    waves = schedule_waves(steps)
    assert [s["id"] for s in waves[0]] == [1]
    assert [s["id"] for s in waves[1]] == [2]
