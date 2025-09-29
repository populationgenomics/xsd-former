import functools
import itertools
from collections.abc import Iterable, Iterator

from xsdformer import generator
from xsdformer.xsd import text, xsd


class ProtobufGenerator:
  def header(self) -> Iterable[str]:
    return ['syntax = "proto3";', 'import "google/protobuf/timestamp.proto";']

  def footer(self) -> Iterable[str]:
    return []

  def begin_namespace(self, namespace: str) -> Iterable[str]:
    self._namespace = namespace
    return [f"package {namespace};"]

  def end_namespace(self, namespace: str) -> Iterable[str]:
    del namespace
    return []

  def enum(self, enum_def: xsd.Enumeration) -> Iterable[str]:
    yield f"enum {enum_def.name} {{"
    for field in enum_def.field_iter():
      yield f"  {field.name} = {field.num};"
    yield "}"

  def definition(self, type_def: xsd.TypeDefinition) -> Iterable[str]:
    yield from self._definition(type_def)

  @functools.singledispatchmethod
  def _definition(self, type_def: xsd.TypeDefinition) -> Iterable[str]:
    raise NotImplementedError(f"Not implemented for {type_def=}")

  @_definition.register
  def _(self, msg_def: xsd.MapType) -> Iterable[str]:
    del msg_def
    yield from iter([])

  @_definition.register
  def _(self, msg_def: xsd.Message) -> Iterable[str]:
    yield from self.message(msg_def)

  @_definition.register
  def _(self, enum_def: xsd.Enumeration) -> Iterable[str]:
    yield from self.enum(enum_def)

  @functools.singledispatchmethod
  def message_field(
    self,
    field_def: xsd.FieldDefinition,
    path: tuple[str, ...],
  ) -> Iterable[str]:
    raise NotImplementedError(f"Not implemented for {field_def=}")

  @message_field.register
  def _(self, field_def: xsd.Choice, path: tuple[str, ...]) -> Iterable[str]:
    oneof = all(not f.is_repeated for f in field_def.get_fields())

    inner = itertools.chain.from_iterable(
      self.message_field(inner, path) for inner in field_def.content
    )
    if not oneof:
      yield from inner
    else:
      yield "oneof oneof_name {"
      yield from text.indent(inner)
      yield "}"

  @message_field.register
  def _(self, field_def: xsd.Seq, path: tuple[str, ...]) -> Iterable[str]:
    for inner in field_def.content:
      yield from self.message_field(inner, path)

  @message_field.register
  def _(self, field_def: xsd.Field, path: tuple[str, ...]) -> Iterable[str]:
    if field_def.documentation:
      yield from text.render_comment(field_def.documentation)
    type_str = field_def.proto_type_str(path)
    yield f"{type_str} {field_def.name} = {field_def.num};"

  def message(self, msg_def: xsd.Message) -> Iterable[str]:
    yield f"message {msg_def.name} {{"
    for inner in msg_def.inner_types():
      yield from text.indent(self.definition(inner))
    for field_def in msg_def.content:
      yield from text.indent(self.message_field(field_def, msg_def.path))
    yield "}"


def generate(
  namespace: str,
  type_defs: tuple[xsd.TypeDefinition, ...],
) -> Iterator[str]:
  yield from generator.generate_with(ProtobufGenerator(), namespace, type_defs)
