from greeting import greet


def test_greet() -> None:
    assert greet("pytest") == "hello, pytest"
