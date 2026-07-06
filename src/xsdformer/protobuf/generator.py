from __future__ import annotations

import functools
import itertools
from collections.abc import Iterable, Iterator

from xsdformer import generator, transforms
from xsdformer.xsd import text, xsd


def _needs_proto3_optional(field_def: xsd.Field) -> bool:
    """Whether a singular field needs the proto3 `optional` keyword (ADR 0002 R1).

    proto3 gives singular scalar and enum fields no field presence: an absent
    value is indistinguishable from the type default (`""`/`0`/`false`/the zero
    enum member). The dialect renders a `(0,1)` field as pydantic `T | None`, so
    without a hasbit a `pydantic→proto→pydantic` round-trip collapses `None` into
    the default. Emitting `optional` gives `HasField`/`ClearField`, making the
    round-trip lossless.

    Left alone, because they already carry presence or have no `None`:
    message-typed fields (including `xs:date`→`Timestamp`), maps, and repeated
    fields. Fields inside a proto `oneof` likewise get presence from the oneof and
    cannot carry `optional` (a proto3 syntax error), so the caller suppresses it.
    """
    if field_def.is_repeated or field_def.computed_occurs[0] != 0:
        return False
    proto_type = field_def.proto_type
    if isinstance(proto_type, xsd.AtomicType):
        # AtomicType.DATE maps to google.protobuf.Timestamp, a message with presence.
        return proto_type is not xsd.AtomicType.DATE
    return isinstance(proto_type, xsd.Enumeration)


class ProtobufGenerator:
    def header(self) -> Iterable[str]:
        return ['syntax = "proto3";', 'import "google/protobuf/timestamp.proto";']

    def footer(self) -> Iterable[str]:
        return []

    def begin_namespace(self, namespace: str) -> Iterable[str]:
        self._namespace = namespace
        return [f'package {namespace};']

    def end_namespace(self, namespace: str) -> Iterable[str]:
        del namespace
        return []

    def enum(self, enum_def: xsd.Enumeration) -> Iterable[str]:
        if enum_def.documentation:
            yield from text.render_comment(enum_def.documentation)
        yield f'enum {enum_def.name} {{'
        for field in enum_def.field_iter():
            yield f'  {field.name} = {field.num};'
        yield '}'

    def definition(self, type_def: xsd.TypeDefinition) -> Iterable[str]:
        yield from self._definition(type_def)

    @functools.singledispatchmethod
    def _definition(self, type_def: xsd.TypeDefinition) -> Iterable[str]:
        raise NotImplementedError(f'Not implemented for {type_def=}')

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

    def message_field(
        self,
        field_def: xsd.FieldDefinition,
        path: tuple[str, ...],
        *,
        in_oneof: bool = False,
    ) -> Iterable[str]:
        return self._message_field(field_def, path, in_oneof=in_oneof)

    @functools.singledispatchmethod
    def _message_field(
        self,
        field_def: xsd.FieldDefinition,
        path: tuple[str, ...],
        *,
        in_oneof: bool = False,
    ) -> Iterable[str]:
        raise NotImplementedError(f'Not implemented for {field_def=}')

    @_message_field.register
    def _(self, field_def: xsd.Choice, path: tuple[str, ...], *, in_oneof: bool = False) -> Iterable[str]:
        # Only generate a oneof when every branch is a single leaf Field. If any
        # branch is a Seq/Choice container (multiple fields that can coexist within
        # that branch), a oneof would incorrectly make them mutually exclusive.
        oneof = (
            not in_oneof
            and all(not f.is_repeated for f in field_def.get_fields())
            and all(isinstance(branch, xsd.Field) for branch in field_def.content)
        )

        inner = itertools.chain.from_iterable(
            self.message_field(inner, path, in_oneof=in_oneof or oneof) for inner in field_def.content
        )
        if not oneof:
            yield from inner
        else:
            yield 'oneof oneof_name {'
            yield from text.indent(inner)
            yield '}'

    @_message_field.register
    def _(self, field_def: xsd.Seq, path: tuple[str, ...], *, in_oneof: bool = False) -> Iterable[str]:
        for inner in field_def.content:
            yield from self.message_field(inner, path, in_oneof=in_oneof)

    @_message_field.register
    def _(self, field_def: xsd.Field, path: tuple[str, ...], *, in_oneof: bool = False) -> Iterable[str]:
        if field_def.transform_hint is transforms.TransformHint.DROPPED:
            return
        if field_def.name in self._emitted_fields:
            return
        self._emitted_fields.add(field_def.name)
        if field_def.documentation:
            yield from text.render_comment(field_def.documentation)
        type_str = field_def.proto_type_str(path)
        optional = 'optional ' if not in_oneof and _needs_proto3_optional(field_def) else ''
        yield f'{optional}{type_str} {field_def.name} = {field_def.num};'

    def message(self, msg_def: xsd.Message) -> Iterable[str]:
        saved = getattr(self, '_emitted_fields', None)
        self._emitted_fields: set[str | None] = set()
        if msg_def.documentation:
            yield from text.render_comment(msg_def.documentation)
        yield f'message {msg_def.name} {{'
        for inner in msg_def.inner_types():
            yield from text.indent(self.definition(inner))
        for field_def in msg_def.content:
            yield from text.indent(self.message_field(field_def, msg_def.path))
        yield '}'
        self._emitted_fields = saved or set()


def generate(
    namespace: str,
    type_defs: tuple[xsd.TypeDefinition, ...],
) -> Iterator[str]:
    yield from generator.generate_with(ProtobufGenerator(), namespace, type_defs)
