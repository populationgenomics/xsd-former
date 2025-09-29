import pytest

from xsdformer.xsd import text


@pytest.mark.parametrize(
  ("input_text", "expected_output"),
  [
    ("HelloWorld", "hello_world"),
    ("hello_world", "hello_world"),
    ("Hello World", "hello_world"),
    ("HelloWorld123", "hello_world_123"),
  ],
)
def test_snake_case(input_text: str, expected_output: str) -> None:
  assert text.snake_case(input_text) == expected_output


@pytest.mark.parametrize(
  ("input_text", "expected_output"),
  [
    ("hello_world", "HelloWorld"),
    ("HelloWorld", "HelloWorld"),
    ("hello world", "HelloWorld"),
    ("hello_world_123", "HelloWorld123"),
  ],
)
def test_pascal_case(input_text: str, expected_output: str) -> None:
  assert text.pascal_case(input_text) == expected_output


@pytest.mark.parametrize(
  ("input_text", "expected_output"),
  [
    (["hello", "world"], "hello world"),
    (["  hello  ", "  world  "], "hello world"),
    (["hello", "\n", "world"], "hello world"),
  ],
)
def test_normalize_whitespace(input_text: str, expected_output: str) -> None:
  assert text.normalize_whitespace(input_text) == expected_output


def test_keep() -> None:
  assert text.snake_case(text.keep("KeepThis")) == "KeepThis"
  assert text.pascal_case(text.keep("keep_this")) == "keep_this"
