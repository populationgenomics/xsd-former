from collections.abc import Iterable, Iterator, Sequence
from typing import Protocol

from xsdformer.xsd import xsd


class IGenerator(Protocol):
  def header(self) -> Iterable[str]: ...
  def footer(self) -> Iterable[str]: ...
  def begin_namespace(self, namespace: str) -> Iterable[str]: ...
  def end_namespace(self, namespace: str) -> Iterable[str]: ...

  def definition(self, type_def: xsd.TypeDefinition) -> Iterable[str]: ...


def generate_with(
  gen: IGenerator, namespace: str, defs: Sequence[xsd.TypeDefinition],
) -> Iterator[str]:
  yield from gen.header()
  yield from gen.begin_namespace(namespace)

  for t in defs:
    if t.enclosing_type is not None:
      continue
    yield from gen.definition(t)

  yield from gen.end_namespace(namespace)
  yield from gen.footer()
