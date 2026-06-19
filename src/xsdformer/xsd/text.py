import functools
import re
from collections.abc import Iterable, Iterator, Sequence


class _Exact(str):
    pass


def keep(text: str) -> str:
    return _Exact(text)


def _words(text: str) -> list[str]:
    """Splits a string into words.

    Handles camelCase and removes non-alphanumeric characters.

    Args:
      text: The string to split.

    Returns:
      A list of words.
    """
    text = re.sub(r"([a-z])([A-Z0-9])", r"\1 \2", text)
    text = re.sub(r"([0-9])([a-zA-Z])", r"\1 \2", text)
    text = re.sub(r"[^0-9a-zA-Z]", " ", text)
    return text.strip().split()


def normalize_whitespace(text: Sequence[str]) -> str:
    """Normalizes whitespace in a sequence of strings.

    Joins the strings with spaces, then collapses consecutive whitespace
    characters into a single space, and finally strips leading/trailing
    whitespace.

    Args:
      text: A sequence of strings.

    Returns:
      A string with normalized whitespace.
    """
    return re.sub(r"\s+", " ", " ".join(text)).strip()


@functools.singledispatch
def snake_case(text: str) -> str:
    """Converts an identifier to snake_case.

    Strings wrapped in an _Exact are retained.

    Args:
      text: The string to convert.

    Returns:
      A string in snake_case.
    """
    return "_".join([w.lower() for w in _words(text)])


@snake_case.register
def _(text: _Exact) -> str:
    return text


@functools.singledispatch
def pascal_case(text: str) -> str:
    """Converts an identifiera to PascalCase.

    Strings wrapped in an _Exact are retained.

    Args:
      text: The string to convert.

    Returns:
      A string in PascalCase.
    """
    return "".join([w.capitalize() for w in _words(text)])


@pascal_case.register
def _(text: _Exact) -> str:
    return text


def indent(text: Iterable[str], *, indent: str = "  ") -> Iterator[str]:
    for t in text:
        yield indent + t


def render_comment(comment: str) -> Iterable[str]:
    for line in comment.split("\n"):
        yield f"// {line}"


def render_doc_comment(doc: str) -> Iterable[str]:
    """Renders a JSDoc-style `/** ... */` doc-comment.

    TypeSpec emitters promote these to schema descriptions. Single-line docs are
    rendered compactly (`/** text */`); multi-line docs use the block form.

    Args:
      doc: The documentation text.

    Returns:
      The doc-comment lines.
    """
    # Break any `*/` in the text so it can't terminate the comment block early.
    lines = doc.replace("*/", "* /").split("\n")
    if len(lines) == 1:
        yield f"/** {lines[0]} */"
        return
    yield "/**"
    for line in lines:
        yield f" * {line}"
    yield " */"
