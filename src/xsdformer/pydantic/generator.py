"""Pydantic v2 emitter — ADR 0002 slice 1.

Emits a module of clean-dialect pydantic v2 models from the same IR as the
protobuf/TypeSpec generators. The dialect is ADR 0001's default-mode TypeSpec
rendered as pydantic v2 (ADR 0002 "Dialect"):

- **Enums:** `str, Enum` subclasses; member name = `EnumField.name` (the
  SCREAMING proto value name — the converter's identity key), member value =
  `xml_value` (the pretty JSON string). Synthesized `*_UNSPECIFIED = ""` first.
- **Fields:** snake_case; `(1,1)` -> `T`, `(0,1)` -> `T | None = None`,
  repeated -> `list[T] = []`, `MapType` -> `dict[str, V] = {}`.
- **Scalars:** `xs:date` -> `datetime`, per ADR 0001.
- **Nested types:** hoisted to module scope as `Parent_Child` (proto nests them;
  the slice-3 converter bridges the two).
- **Choice:** flattened to independent optional fields (no mutual-exclusion in
  the model — see ADR 0002 "Choice enforcement").

Field-level documentation is intentionally dropped: the clean dialect carries
only structure, and the equivalence gate (ADR 0002 slice 5) normalizes away
`title`/`description`. Type-level documentation is kept as a class docstring.

Python keyword field names (`class`, `import`, ...) can't be attribute names, so
they are suffixed with `_` and given a `Field(alias=...)` carrying the original
name; the model gains `model_config = ConfigDict(populate_by_name=True)` so the
slice-3 converter can construct by attribute name. This mirrors what
`datamodel-code-generator` (the slice-5 oracle) does for the same collision.
"""

import functools
from collections.abc import Iterable, Iterator

from xsdformer.pydantic._naming import attr_name as _attr_name
from xsdformer.pydantic._naming import type_name as _type_name
from xsdformer.transforms import TransformHint
from xsdformer.xsd import xsd

# AtomicType -> pydantic/Python type (ADR 0001 "Scalars", rendered for Python).
_PY_SCALAR = {
    xsd.AtomicType.ID: "str",
    xsd.AtomicType.URI: "str",
    xsd.AtomicType.STRING: "str",
    xsd.AtomicType.SIMPLEANY: "str",
    xsd.AtomicType.COMPLEXANY: "str",
    xsd.AtomicType.INT8: "int",
    xsd.AtomicType.UINT8: "int",
    xsd.AtomicType.INT16: "int",
    xsd.AtomicType.UINT16: "int",
    xsd.AtomicType.INT32: "int",
    xsd.AtomicType.UINT32: "int",
    xsd.AtomicType.INT64: "int",
    xsd.AtomicType.UINT64: "int",
    xsd.AtomicType.FLOAT: "float",
    xsd.AtomicType.DOUBLE: "float",
    xsd.AtomicType.BOOL: "bool",
    xsd.AtomicType.BYTES: "bytes",
    xsd.AtomicType.DATE: "datetime",
}


def _field_type(field_def: xsd.Field) -> str:
    """The Python type for a field, including any map/collection shape."""
    proto_type = field_def.proto_type
    if isinstance(proto_type, xsd.AtomicType):
        return _PY_SCALAR[proto_type]
    if isinstance(proto_type, xsd.MapType):
        # proto map<K, V> -> dict[str, V]; proto-JSON stringifies every map key,
        # so a string-keyed dict is JSON-correct for any proto map (ADR 0001).
        return f"dict[str, {_PY_SCALAR[proto_type.value_type]}]"
    return _type_name(proto_type)


def _iter_message_fields(
    content: Iterable[xsd.FieldDefinition],
    *,
    in_choice: bool = False,
) -> Iterator[tuple[xsd.Field, bool]]:
    """Walks a message's content tree, yielding each leaf field paired with
    whether a `Choice` encloses it.

    `Choice` members flatten to optional fields (ADR 0002): a proto `oneof` and N
    optional fields are wire- and proto-JSON-identical, so flattening preserves
    the governing invariant.
    """
    for field_def in content:
        match field_def:
            case xsd.Choice():
                yield from _iter_message_fields(field_def.content, in_choice=True)
            case xsd.FieldContainer():  # Seq
                yield from _iter_message_fields(field_def.content, in_choice=in_choice)
            case xsd.Field():
                yield field_def, in_choice


def _indent_body(lines: Iterable[str]) -> Iterator[str]:
    """Indents a class body by 4 spaces, leaving blank lines empty."""
    for line in lines:
        yield f"    {line}" if line else ""


def _docstring(doc: str) -> str:
    """Renders type documentation as a single-line class docstring.

    `_get_documentation` collapses all whitespace to single spaces, so the doc is
    always one line. Backslashes and any embedded triple-quote are escaped so the
    string literal stays valid; a trailing quote is separated from the closer.
    """
    safe = doc.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    if safe.endswith('"'):
        safe += " "
    return f'"""{safe}"""'


class PydanticGenerator:
    def __init__(
        self,
        *,
        needs_enum: bool = False,
        needs_datetime: bool = False,
        needs_model: bool = False,
        needs_field: bool = False,
    ) -> None:
        self._needs_enum = needs_enum
        self._needs_datetime = needs_datetime
        self._needs_model = needs_model
        self._needs_field = needs_field

    def header(self) -> Iterable[str]:
        # `from __future__ import annotations` defers annotation evaluation, so
        # hoisted forward references and the `T | None` union syntax need no
        # special handling. Imports follow isort grouping (future / stdlib /
        # third-party), each group only emitted when something in it is used.
        lines = ["from __future__ import annotations"]
        stdlib = []
        if self._needs_datetime:
            stdlib.append("from datetime import datetime")
        if self._needs_enum:
            stdlib.append("from enum import Enum")
        if stdlib:
            lines.append("")
            lines.extend(stdlib)
        pydantic_names = []
        if self._needs_model:
            pydantic_names.append("BaseModel")
        if self._needs_field:
            pydantic_names.extend(["ConfigDict", "Field"])
        if pydantic_names:
            lines.append("")
            lines.append(f"from pydantic import {', '.join(pydantic_names)}")
        return lines

    def footer(self) -> Iterable[str]:
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
        del map_def  # Top-level maps emit nothing; they surface as `dict[str, V]`.
        return ()

    def enum(self, enum_def: xsd.Enumeration) -> Iterable[str]:
        yield f"class {_type_name(enum_def)}(str, Enum):"
        body: list[str] = []
        if enum_def.documentation:
            body.append(_docstring(enum_def.documentation))
            body.append("")
        for field_def in enum_def.field_iter():
            value = "" if field_def.xml_value is None else field_def.xml_value
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            body.append(f'{field_def.name} = "{escaped}"')
        yield from _indent_body(body)

    def message(self, msg_def: xsd.Message) -> Iterable[str]:
        yield f"class {_type_name(msg_def)}(BaseModel):"
        field_lines: list[str] = []
        has_alias = False
        emitted: set[str | None] = set()
        for field_def, in_choice in _iter_message_fields(msg_def.content):
            if field_def.transform_hint is TransformHint.DROPPED:
                continue
            if field_def.name in emitted:
                continue
            emitted.add(field_def.name)
            line, used_alias = self._field(field_def, force_optional=in_choice)
            has_alias = has_alias or used_alias
            field_lines.append(line)

        body: list[str] = []
        if msg_def.documentation:
            body.append(_docstring(msg_def.documentation))
            body.append("")
        if has_alias:
            # `populate_by_name` lets the slice-3 converter construct by attribute
            # name even though aliased fields carry the original XML name.
            body.append("model_config = ConfigDict(populate_by_name=True)")
            body.append("")
        body.extend(field_lines)
        if not body:
            body.append("pass")
        yield from _indent_body(body)

    def _field(self, field_def: xsd.Field, *, force_optional: bool) -> tuple[str, bool]:
        """Renders one field; returns `(line, used_alias)`."""
        attr = _attr_name(field_def.name)
        alias = field_def.name if attr != field_def.name else None
        type_str = _field_type(field_def)
        proto_type = field_def.proto_type

        if isinstance(proto_type, xsd.MapType):
            kind = "map"
        elif field_def.is_repeated:
            kind = "repeated"
            type_str = f"list[{type_str}]"
        elif force_optional or field_def.computed_occurs[0] == 0:
            kind = "optional"
            type_str = f"{type_str} | None"
        else:
            kind = "required"

        if alias is None:
            default = {"map": " = {}", "repeated": " = []", "optional": " = None", "required": ""}[kind]
            return f"{attr}: {type_str}{default}", False

        field_call = {
            "map": f'Field(default_factory=dict, alias="{alias}")',
            "repeated": f'Field(default_factory=list, alias="{alias}")',
            "optional": f'Field(default=None, alias="{alias}")',
            "required": f'Field(alias="{alias}")',
        }[kind]
        return f"{attr}: {type_str} = {field_call}", True


def _scan(type_defs: tuple[xsd.TypeDefinition, ...]) -> dict[str, bool]:
    """Determines which imports the emitted module needs."""
    flags = {"needs_enum": False, "needs_datetime": False, "needs_model": False, "needs_field": False}
    for type_def in type_defs:
        if isinstance(type_def, xsd.Enumeration):
            flags["needs_enum"] = True
        elif isinstance(type_def, xsd.Message):
            flags["needs_model"] = True
            for field_def, _ in _iter_message_fields(type_def.content):
                if field_def.transform_hint is TransformHint.DROPPED:
                    continue
                proto_type = field_def.proto_type
                if proto_type is xsd.AtomicType.DATE:
                    flags["needs_datetime"] = True
                if isinstance(proto_type, xsd.MapType) and proto_type.value_type is xsd.AtomicType.DATE:
                    flags["needs_datetime"] = True
                if _attr_name(field_def.name) != field_def.name:
                    flags["needs_field"] = True
    return flags


def generate(
    namespace: str,
    type_defs: tuple[xsd.TypeDefinition, ...],
) -> Iterator[str]:
    # Pydantic models are module-scoped — there is no namespace construct, so the
    # namespace is unused. Like TypeSpec (and unlike proto/JSON-Schema, which nest
    # enclosed types inline), every type is hoisted to module scope under its
    # `Parent_Child` name, so emit all definitions, including enclosed ones.
    del namespace
    gen = PydanticGenerator(**_scan(type_defs))
    yield from gen.header()
    for type_def in type_defs:
        body = list(gen.definition(type_def))
        if body:
            # Two blank lines between top-level definitions (PEP 8 / ruff).
            yield ""
            yield ""
            yield from body
