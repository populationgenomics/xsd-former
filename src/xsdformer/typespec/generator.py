"""TypeSpec (`.tsp`) emitter — slice 2 walking skeleton (ADR 0001).

Emits a single namespace of flat `model`s with scalar fields and cardinality,
sourced from the same IR as the protobuf generator. Enums (slice 3), nesting
and `Choice` (slice 4), maps (slice 5), and `--proto-compat` (slice 6) are not
yet handled; their `TypeDefinition`s currently emit nothing.
"""

import functools
from collections.abc import Iterable, Iterator

from xsdformer import generator
from xsdformer.transforms import TransformHint
from xsdformer.xsd import text, xsd

# AtomicType -> TypeSpec scalar (ADR 0001 "Scalars"). Diverges from
# `AtomicType.proto_str` where TypeSpec spells the type differently:
# float32/float64 vs float/double, boolean vs bool, utcDateTime vs Timestamp.
_TSP_SCALAR = {
    xsd.AtomicType.ID: "string",
    xsd.AtomicType.URI: "string",
    xsd.AtomicType.STRING: "string",
    xsd.AtomicType.SIMPLEANY: "string",
    xsd.AtomicType.COMPLEXANY: "string",
    xsd.AtomicType.INT8: "int8",
    xsd.AtomicType.UINT8: "uint8",
    xsd.AtomicType.INT16: "int16",
    xsd.AtomicType.UINT16: "uint16",
    xsd.AtomicType.INT32: "int32",
    xsd.AtomicType.UINT32: "uint32",
    xsd.AtomicType.INT64: "int64",
    xsd.AtomicType.UINT64: "uint64",
    xsd.AtomicType.FLOAT: "float32",
    xsd.AtomicType.DOUBLE: "float64",
    xsd.AtomicType.BOOL: "boolean",
    xsd.AtomicType.BYTES: "bytes",
    xsd.AtomicType.DATE: "utcDateTime",
}


def _namespace_name(namespace: str) -> str:
    """Renders a (possibly dotted) package as a TypeSpec namespace identifier."""
    return ".".join(text.pascal_case(part) for part in namespace.split("."))


def _field_type(field_def: xsd.Field) -> str:
    """The TypeSpec type for a field, without cardinality."""
    proto_type = field_def.proto_type
    if isinstance(proto_type, xsd.AtomicType):
        return _TSP_SCALAR[proto_type]
    # A reference to another type. Nested types are hoisted to `Parent_Child` in
    # slice 4; joining the path components reproduces that name for the flat case.
    return "_".join(proto_type.path)


class TypeSpecGenerator:
    def header(self) -> Iterable[str]:
        return []

    def footer(self) -> Iterable[str]:
        return []

    def begin_namespace(self, namespace: str) -> Iterable[str]:
        self._namespace = namespace
        return [f"namespace {_namespace_name(namespace)};"]

    def end_namespace(self, namespace: str) -> Iterable[str]:
        del namespace
        return []

    def definition(self, type_def: xsd.TypeDefinition) -> Iterable[str]:
        yield from self._definition(type_def)

    @functools.singledispatchmethod
    def _definition(self, type_def: xsd.TypeDefinition) -> Iterable[str]:
        raise NotImplementedError(f"Not implemented for {type_def=}")

    @_definition.register
    def _(self, msg_def: xsd.Message) -> Iterable[str]:
        yield from self.message(msg_def)

    @_definition.register
    def _(self, enum_def: xsd.Enumeration) -> Iterable[str]:
        del enum_def  # Enums: slice 3.
        yield from iter([])

    @_definition.register
    def _(self, map_def: xsd.MapType) -> Iterable[str]:
        del map_def  # Top-level maps emit nothing (slice 5).
        yield from iter([])

    def field(self, field_def: xsd.Field) -> Iterable[str]:
        type_str = _field_type(field_def)
        if field_def.is_repeated:
            yield f"{field_def.name}: {type_str}[];"
        elif field_def.computed_occurs[0] == 0:
            yield f"{field_def.name}: {type_str}?;"
        else:
            yield f"{field_def.name}: {type_str};"

    def message(self, msg_def: xsd.Message) -> Iterable[str]:
        yield ""
        yield f"model {msg_def.name} {{"
        emitted: set[str | None] = set()
        for field_def in msg_def.get_fields():
            if field_def.transform_hint is TransformHint.DROPPED:
                continue
            if field_def.name in emitted:
                continue
            emitted.add(field_def.name)
            yield from text.indent(self.field(field_def))
        yield "}"


def generate(
    namespace: str,
    type_defs: tuple[xsd.TypeDefinition, ...],
) -> Iterator[str]:
    yield from generator.generate_with(TypeSpecGenerator(), namespace, type_defs)
