"""TypeSpec (`.tsp`) emitter — slices 2-6 (ADR 0001).

Emits a single namespace of flat `model`s with scalar fields and cardinality,
plus string-valued `enum`s and JSDoc-style doc-comments, sourced from the same
IR as the protobuf generator. Nested (enclosed) types are hoisted to the
top-level namespace as `Parent_Child`, `Choice` members flatten to optional
properties, and map-typed fields surface as `Record<V>`.

`--proto-compat` adds the `@typespec/protobuf` decorations (`import`/`using`,
`@package`, `@field`) and switches enums to integer-valued members (member
names preserved, numbers = the IR's enum numbers, zero member first) — the form
`@typespec/protobuf` requires, per ADR 0001 slice 1 — so that `tsp->proto` can
serve as the `xsd->proto` ≡ `xsd->tsp->proto` regression check.
"""

from __future__ import annotations

import functools
from collections.abc import Iterable, Iterator

from xsdformer import transforms
from xsdformer.xsd import text, xsd

# AtomicType -> TypeSpec scalar (ADR 0001 "Scalars"). Diverges from
# `AtomicType.proto_str` where TypeSpec spells the type differently:
# float32/float64 vs float/double, boolean vs bool, utcDateTime vs Timestamp.
_TSP_SCALAR = {
    xsd.AtomicType.ID: 'string',
    xsd.AtomicType.URI: 'string',
    xsd.AtomicType.STRING: 'string',
    xsd.AtomicType.SIMPLEANY: 'string',
    xsd.AtomicType.COMPLEXANY: 'string',
    xsd.AtomicType.INT8: 'int8',
    xsd.AtomicType.UINT8: 'uint8',
    xsd.AtomicType.INT16: 'int16',
    xsd.AtomicType.UINT16: 'uint16',
    xsd.AtomicType.INT32: 'int32',
    xsd.AtomicType.UINT32: 'uint32',
    xsd.AtomicType.INT64: 'int64',
    xsd.AtomicType.UINT64: 'uint64',
    xsd.AtomicType.FLOAT: 'float32',
    xsd.AtomicType.DOUBLE: 'float64',
    xsd.AtomicType.BOOL: 'boolean',
    xsd.AtomicType.BYTES: 'bytes',
    xsd.AtomicType.DATE: 'utcDateTime',
}

# TypeSpec reserved keywords (from the `@typespec/compiler` scanner). A property
# name that collides with one is a syntax error unless backtick-quoted. Field
# names are snake_case/lowercase so they can collide; model/enum names are
# PascalCase and enum members SCREAMING_CASE, so neither does. Backtick-quoting
# is valid for any identifier, so escaping this (possibly over-broad) set is safe.
_TSP_RESERVED = frozenset(
    {
        'alias',
        'arg',
        'array',
        'async',
        'auto',
        'const',
        'context',
        'dec',
        'declare',
        'else',
        'enum',
        'env',
        'extends',
        'extern',
        'false',
        'flag',
        'fn',
        'if',
        'impl',
        'implements',
        'import',
        'init',
        'interface',
        'internal',
        'is',
        'keyof',
        'local',
        'macro',
        'metadata',
        'mod',
        'model',
        'module',
        'namespace',
        'never',
        'op',
        'package',
        'partial',
        'private',
        'projection',
        'prop',
        'property',
        'protected',
        'pub',
        'public',
        'record',
        'return',
        'satisfies',
        'scalar',
        'scenario',
        'sealed',
        'self',
        'statemachine',
        'struct',
        'sub',
        'super',
        'sym',
        'this',
        'trait',
        'true',
        'typeof',
        'typeref',
        'union',
        'unknown',
        'using',
        'valueof',
        'void',
        'with',
    },
)


def _escape_field_name(name: str | None) -> str | None:
    """Backtick-quotes a field name that collides with a TypeSpec keyword."""
    return f'`{name}`' if name in _TSP_RESERVED else name


_FIRST_PRINTABLE = 0x20  # Code points below this are C0 control characters.


def _tsp_string(value: str) -> str:
    r"""A fully-escaped TypeSpec double-quoted string literal for `value`.

    Escapes backslash/quote and every control character so the literal is always
    valid TypeSpec. Control chars use TypeSpec's `\\u{hex}` form — JSON's bare
    `\\uXXXX` is not valid TypeSpec, so `json.dumps` can't be used here.
    """
    simple = {'\\': '\\\\', '"': '\\"', '\n': '\\n', '\r': '\\r', '\t': '\\t'}
    out = [simple.get(ch) or (f'\\u{{{ord(ch):x}}}' if ord(ch) < _FIRST_PRINTABLE else ch) for ch in value]
    return '"' + ''.join(out) + '"'


def _namespace_name(namespace: str) -> str:
    """Renders a (possibly dotted) package as a TypeSpec namespace identifier."""
    return '.'.join(text.pascal_case(part) for part in namespace.split('.'))


def _type_name(type_def: xsd.TypeDefinition) -> str:
    """The hoisted top-level name for a type definition.

    Nested (enclosed) types are hoisted to the namespace as `Parent_Child` — the
    PascalCase path components joined by `_`. Top-level types have a one-element
    path, so this is just their name.
    """
    return '_'.join(type_def.path)


def _scalar(atomic: xsd.AtomicType, *, proto_compat: bool) -> str:
    """The TypeSpec scalar for an `AtomicType`, mode-aware.

    Default mode uses TypeSpec-native scalars (`_TSP_SCALAR`). Proto-compat mode
    overrides `DATE`: `@typespec/protobuf` has no proto mapping for the native
    `utcDateTime` scalar (`unsupported-field-type`), so it must reference the
    well-known `google.protobuf.Timestamp` model instead — the same proto type
    the protobuf generator emits, keeping `xsd->proto` ≡ `xsd->tsp->proto`.
    """
    if proto_compat and atomic is xsd.AtomicType.DATE:
        return 'WellKnown.Timestamp'
    return _TSP_SCALAR[atomic]


def _field_type(field_def: xsd.Field, *, proto_compat: bool) -> str:
    """The TypeSpec type for a field, including any map/collection shape."""
    proto_type = field_def.proto_type
    if isinstance(proto_type, xsd.AtomicType):
        return _scalar(proto_type, proto_compat=proto_compat)
    if isinstance(proto_type, xsd.MapType):
        # A proto map<K, V> -> `Record<V>` (string-keyed). proto-JSON stringifies
        # every map key, so a string-keyed Record is JSON-correct for any proto
        # map; non-string proto key types are deferred (ADR 0001).
        return f'Record<{_scalar(proto_type.value_type, proto_compat=proto_compat)}>'
    # A reference to another (possibly hoisted) type.
    return _type_name(proto_type)


def _iter_message_fields(
    content: Iterable[xsd.FieldDefinition],
    *,
    in_choice: bool = False,
) -> Iterator[tuple[xsd.Field, bool]]:
    """Yield each leaf field of a message's content tree, paired with whether a `Choice` encloses it.

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
    def __init__(self, *, proto_compat: bool = False) -> None:
        self._proto_compat = proto_compat

    def header(self) -> Iterable[str]:
        if self._proto_compat:
            return ['import "@typespec/protobuf";', 'using Protobuf;', '']
        return []

    def footer(self) -> Iterable[str]:
        return []

    def begin_namespace(self, namespace: str) -> Iterable[str]:
        if self._proto_compat:
            # `@package` carries the raw (possibly dotted) name through to proto's
            # `package`, matching the protobuf generator's resolution; the TypeSpec
            # `namespace` identifier is the PascalCased form.
            return [
                f'@package({{name: "{namespace}"}})',
                f'namespace {_namespace_name(namespace)};',
            ]
        return [f'namespace {_namespace_name(namespace)};']

    def end_namespace(self, namespace: str) -> Iterable[str]:
        del namespace
        return []

    def definition(self, type_def: xsd.TypeDefinition) -> Iterable[str]:
        yield from self._definition(type_def)

    @functools.singledispatchmethod
    def _definition(self, type_def: xsd.TypeDefinition) -> Iterable[str]:
        raise NotImplementedError(f'Not implemented for {type_def=}')

    @_definition.register
    def _(self, msg_def: xsd.Message) -> Iterable[str]:
        yield from self.message(msg_def)

    @_definition.register
    def _(self, enum_def: xsd.Enumeration) -> Iterable[str]:
        yield from self.enum(enum_def)

    @_definition.register
    def _(self, map_def: xsd.MapType) -> Iterable[str]:
        del map_def  # Top-level maps emit nothing; they surface as `Record<V>`.
        return ()

    def enum(self, enum_def: xsd.Enumeration) -> Iterable[str]:
        # Default mode: string-valued members so a single artifact serves
        # pydantic/zod. Member name = proto value name (`EnumField.name`); value
        # = `xml_value` (the synthesized zero member has none, so `""`).
        # `--proto-compat`: integer values (`@typespec/protobuf` rejects string
        # members), member names preserved, numbers = the IR's enum numbers.
        yield ''
        if enum_def.documentation:
            yield from text.render_doc_comment(enum_def.documentation)
        yield f'enum {_type_name(enum_def)} {{'
        yield from text.indent(self._enum_members(enum_def))
        yield '}'

    def _enum_members(self, enum_def: xsd.Enumeration) -> Iterable[str]:
        for field_def in enum_def.field_iter():
            if self._proto_compat:
                yield f'{self._proto_member_name(enum_def, field_def)}: {field_def.num},'
            else:
                value = '' if field_def.xml_value is None else field_def.xml_value
                yield f'{field_def.name}: {_tsp_string(value)},'

    @staticmethod
    def _proto_member_name(enum_def: xsd.Enumeration, field_def: xsd.EnumField) -> str:
        """Enum member name for proto-compat, prefixed with the full hoisted path.

        Proto enum values are C++-scoped to the *enclosing* scope, not the enum.
        The protobuf generator nests each enum inside its message, so a bare
        `<LOCAL>_<VALUE>` name (already what `EnumField.name` carries) is
        collision-free there. TypeSpec hoists every enum to the namespace,
        dropping that scoping — so two hoisted enums that share a local name
        (e.g. nested `Sample.Origin` and a top-level `Origin`, both bearing
        `ORIGIN_*`) would collide at package scope. Re-prefixing with the parent
        path components (`SAMPLE_ORIGIN_GERMLINE`) restores uniqueness, mirroring
        proto's own type-name-prefix idiom. Default mode is unaffected: TypeSpec
        enum members aren't C++-scoped, and the converter keys off the bare proto
        value name (ADR 0001). The round-trip normalizer strips this prefix.
        """
        parent_prefix = '_'.join(text.snake_case(part).upper() for part in enum_def.path[:-1])
        return f'{parent_prefix}_{field_def.name}' if parent_prefix else field_def.name

    def field(self, field_def: xsd.Field, *, force_optional: bool = False) -> Iterable[str]:
        if field_def.documentation:
            yield from text.render_doc_comment(field_def.documentation)
        type_str = _field_type(field_def, proto_compat=self._proto_compat)
        # `--proto-compat`: `@field(N)` pins the proto field number to the IR's,
        # so `tsp->proto` reproduces `xsd->proto`'s wire layout.
        prefix = f'@field({field_def.num}) ' if self._proto_compat else ''
        name = _escape_field_name(field_def.name)
        if isinstance(field_def.proto_type, xsd.MapType):
            # `Record<V>` already carries collection shape: a map is required-but-
            # possibly-empty (like repeated -> `T[]`), so no `[]` or optional marker.
            yield f'{prefix}{name}: {type_str};'
        elif field_def.is_repeated:
            yield f'{prefix}{name}: {type_str}[];'
        elif force_optional or field_def.computed_occurs[0] == 0:
            # Optionality is on the property name in TypeSpec (`name?: T`), not a
            # suffix on the type.
            yield f'{prefix}{name}?: {type_str};'
        else:
            yield f'{prefix}{name}: {type_str};'

    def message(self, msg_def: xsd.Message) -> Iterable[str]:
        yield ''
        if msg_def.documentation:
            yield from text.render_doc_comment(msg_def.documentation)
        yield f'model {_type_name(msg_def)} {{'
        emitted: dict[str | None, xsd.Field] = {}
        for field_def, in_choice in _iter_message_fields(msg_def.content):
            if field_def.transform_hint is transforms.TransformHint.DROPPED:
                continue
            if not xsd.register_field(emitted, field_def, _type_name(msg_def)):
                continue
            yield from text.indent(self.field(field_def, force_optional=in_choice))
        yield '}'


def generate(
    namespace: str,
    type_defs: tuple[xsd.TypeDefinition, ...],
    *,
    proto_compat: bool = False,
) -> Iterator[str]:
    # Unlike the proto/JSON-Schema generators (which nest enclosed types inline
    # and so skip them at the top level via `generate_with`), TypeSpec hoists
    # every type to the namespace under its `Parent_Child` name. So emit all
    # definitions, including enclosed ones.
    gen = TypeSpecGenerator(proto_compat=proto_compat)
    yield from gen.header()
    yield from gen.begin_namespace(namespace)
    for type_def in type_defs:
        yield from gen.definition(type_def)
    yield from gen.end_namespace(namespace)
    yield from gen.footer()
