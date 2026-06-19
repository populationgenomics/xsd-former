"""TypeSpec (`.tsp`) emitter — slices 2-4 (ADR 0001).

Emits a single namespace of flat `model`s with scalar fields and cardinality,
plus string-valued `enum`s and JSDoc-style doc-comments, sourced from the same
IR as the protobuf generator. Nested (enclosed) types are hoisted to the
top-level namespace as `Parent_Child`, and `Choice` members flatten to optional
properties. Maps (slice 5) and `--proto-compat` (slice 6) are not yet handled.
"""

import functools
from collections.abc import Iterable, Iterator

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


def _type_name(type_def: xsd.TypeDefinition) -> str:
    """The hoisted top-level name for a type definition.

    Nested (enclosed) types are hoisted to the namespace as `Parent_Child` — the
    PascalCase path components joined by `_`. Top-level types have a one-element
    path, so this is just their name.
    """
    return "_".join(type_def.path)


def _field_type(field_def: xsd.Field) -> str:
    """The TypeSpec type for a field, without cardinality."""
    proto_type = field_def.proto_type
    if isinstance(proto_type, xsd.AtomicType):
        return _TSP_SCALAR[proto_type]
    # A reference to another (possibly hoisted) type.
    return _type_name(proto_type)


def _iter_message_fields(
    content: Iterable[xsd.FieldDefinition],
    *,
    in_choice: bool = False,
) -> Iterator[tuple[xsd.Field, bool]]:
    """Walks a message's content tree, yielding each leaf field paired with
    whether a `Choice` encloses it.

    `Choice` members flatten to optional properties (ADR 0001): a proto `oneof`
    and N optional fields with the same numbers are wire- and proto-JSON-
    identical, so flattening preserves the governing invariant.
    """
    for field_def in content:
        match field_def:
            case xsd.Choice():
                yield from _iter_message_fields(field_def.content, in_choice=True)
            case xsd.FieldContainer():  # Seq
                yield from _iter_message_fields(field_def.content, in_choice=in_choice)
            case xsd.Field():
                yield field_def, in_choice


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
        yield from self.enum(enum_def)

    @_definition.register
    def _(self, map_def: xsd.MapType) -> Iterable[str]:
        del map_def  # Top-level maps emit nothing (slice 5).
        yield from iter([])

    def enum(self, enum_def: xsd.Enumeration) -> Iterable[str]:
        # Default mode: string-valued members so a single artifact serves
        # pydantic/zod. Member name = proto value name (`EnumField.name`); value
        # = `xml_value` (the synthesized zero member has none, so `""`).
        # `--proto-compat` (slice 6) will instead emit integer values.
        yield ""
        if enum_def.documentation:
            yield from text.render_doc_comment(enum_def.documentation)
        yield f"enum {_type_name(enum_def)} {{"
        yield from text.indent(self._enum_members(enum_def))
        yield "}"

    def _enum_members(self, enum_def: xsd.Enumeration) -> Iterable[str]:
        for field_def in enum_def.field_iter():
            value = "" if field_def.xml_value is None else field_def.xml_value
            yield f'{field_def.name}: "{value}",'

    def field(self, field_def: xsd.Field, *, force_optional: bool = False) -> Iterable[str]:
        if field_def.documentation:
            yield from text.render_doc_comment(field_def.documentation)
        type_str = _field_type(field_def)
        if field_def.is_repeated:
            yield f"{field_def.name}: {type_str}[];"
        elif force_optional or field_def.computed_occurs[0] == 0:
            yield f"{field_def.name}: {type_str}?;"
        else:
            yield f"{field_def.name}: {type_str};"

    def message(self, msg_def: xsd.Message) -> Iterable[str]:
        yield ""
        if msg_def.documentation:
            yield from text.render_doc_comment(msg_def.documentation)
        yield f"model {_type_name(msg_def)} {{"
        emitted: set[str | None] = set()
        for field_def, in_choice in _iter_message_fields(msg_def.content):
            if field_def.transform_hint is TransformHint.DROPPED:
                continue
            if field_def.name in emitted:
                continue
            emitted.add(field_def.name)
            yield from text.indent(self.field(field_def, force_optional=in_choice))
        yield "}"


def generate(
    namespace: str,
    type_defs: tuple[xsd.TypeDefinition, ...],
) -> Iterator[str]:
    # Unlike the proto/JSON-Schema generators (which nest enclosed types inline
    # and so skip them at the top level via `generate_with`), TypeSpec hoists
    # every type to the namespace under its `Parent_Child` name. So emit all
    # definitions, including enclosed ones.
    gen = TypeSpecGenerator()
    yield from gen.header()
    yield from gen.begin_namespace(namespace)
    for type_def in type_defs:
        yield from gen.definition(type_def)
    yield from gen.end_namespace(namespace)
    yield from gen.footer()
