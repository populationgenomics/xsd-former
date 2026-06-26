"""proto <-> pydantic converter emitter — ADR 0002 slice 3.

Emits `pydantic_converter.py`: a module of `*_from_proto` / `*_to_proto`
functions bridging the compiled protobuf classes (`*_pb2`) and the pydantic
models (`models.py`). Both sides derive from the same IR, so the conversion is
mechanical (ADR 0002 "proto <-> pydantic converter"):

- **Enums** are keyed by member name — the pydantic member name *is* the proto
  value name — so `proto -> pydantic` indexes `Model[Proto.Name(v)]` and
  `pydantic -> proto` writes `Proto.Value(member.name)`. No remap table.
- **`xs:date`** is a `google.protobuf.Timestamp` on the proto side and a
  `datetime` on the pydantic side: `ToDatetime()` / `FromDatetime()`.
- **Maps** become `dict`, **repeated** fields become `list`.
- **Nested <-> hoisted:** proto `Parent.Child` <-> pydantic `Parent_Child`.
- **Optional scalars/enums** round-trip through proto field presence (ADR 0002
  R1): `proto -> pydantic` reads `... if proto.HasField(f) else None`.
- **Choice** stays flat optionals in the model; `pydantic -> proto` raises when
  more than one branch of a proto `oneof` is set, since proto cannot represent it
  (ADR 0002 "Choice enforcement"). `proto -> pydantic` needs no check — proto
  guarantees at most one.

This is the converter spoke of the proto-centric hub: it is meaningful only
beside a compiled `*_pb2`, so (unlike the clean-dialect models module) it is a
`build`-package artifact, not a `--*-out` target.
"""

import keyword
from collections.abc import Iterable, Iterator

from xsdformer.pydantic._naming import attr_name as _attr_name
from xsdformer.pydantic._naming import type_name as _type_name
from xsdformer.transforms import TransformHint
from xsdformer.xsd import text, xsd


def _field_name(field_def: xsd.Field) -> str:
    """The (always-present) name of a leaf field, narrowed from `str | None`."""
    if field_def.name is None:
        raise ValueError(f"{field_def}: leaf field has no name")
    return field_def.name


def _pget(name: str) -> str:
    """A reference to proto field `name`, via `getattr` if it is a Python keyword.

    Proto identifiers like `class`/`import` are valid proto field names but cannot
    be attribute-accessed in generated Python, so they go through `getattr`.
    """
    return f"proto.{name}" if not keyword.iskeyword(name) else f"getattr(proto, {name!r})"


def _pset(name: str, value: str) -> str:
    """An assignment to proto scalar field `name` (via `setattr` for keywords)."""
    return f"proto.{name} = {value}" if not keyword.iskeyword(name) else f"setattr(proto, {name!r}, {value})"


def _leaf_records(
    msg_def: xsd.Message,
) -> tuple[list[tuple[xsd.Field, bool, bool]], list[list[xsd.Field]]]:
    """Flattens a message's content into leaf fields and proto `oneof` groups.

    Returns `(records, oneof_groups)` where each record is
    `(field, in_choice, in_oneof)` and each group is the list of branch fields a
    proto `oneof` collapses to. The flattening mirrors the pydantic generator
    (Seq/Choice dissolve into flat fields, DROPPED and duplicate names skipped);
    the `oneof` detection mirrors the protobuf generator so presence and the
    multi-branch guard line up with the actual proto shape.
    """
    records: list[tuple[xsd.Field, bool, bool]] = []
    oneof_groups: list[list[xsd.Field]] = []
    emitted: dict[str | None, xsd.Field] = {}

    def _walk(content: Iterable[xsd.FieldDefinition], *, in_choice: bool, in_oneof: bool) -> None:
        for field_def in content:
            match field_def:
                case xsd.Choice():
                    # A Choice becomes a proto `oneof` only when every branch is a
                    # single non-repeated leaf field (same rule as the protobuf
                    # generator); otherwise its branches are plain flat fields.
                    becomes_oneof = (
                        not in_oneof
                        and all(not f.is_repeated for f in field_def.get_fields())
                        and all(isinstance(branch, xsd.Field) for branch in field_def.content)
                    )
                    if becomes_oneof:
                        group = [
                            branch
                            for branch in field_def.content
                            if isinstance(branch, xsd.Field) and branch.transform_hint is not TransformHint.DROPPED
                        ]
                        if len(group) > 1:
                            oneof_groups.append(group)
                        _walk(field_def.content, in_choice=True, in_oneof=True)
                    else:
                        _walk(field_def.content, in_choice=True, in_oneof=in_oneof)
                case xsd.FieldContainer():  # Seq
                    _walk(field_def.content, in_choice=in_choice, in_oneof=in_oneof)
                case xsd.Field():
                    if field_def.transform_hint is TransformHint.DROPPED:
                        continue
                    if not xsd.register_field(emitted, field_def, _type_name(msg_def)):
                        continue
                    records.append((field_def, in_choice, in_oneof))

    _walk(msg_def.content, in_choice=False, in_oneof=False)
    return records, oneof_groups


def _field_kind(field_def: xsd.Field, *, in_choice: bool) -> str:
    """Classifies a leaf field the same way the pydantic generator does."""
    if isinstance(field_def.proto_type, xsd.MapType):
        return "map"
    if field_def.is_repeated:
        return "repeated"
    if in_choice or field_def.computed_occurs[0] == 0:
        return "optional"
    return "required"


def _has_presence(field_def: xsd.Field, *, in_oneof: bool) -> bool:
    """Whether a singular field exposes proto field presence (`HasField`).

    Message-typed fields (including `xs:date` -> `Timestamp`) always have
    presence. Scalars/enums have it via a proto `oneof` or the proto3 `optional`
    keyword, which the protobuf generator emits for `(0,1)` singulars (ADR 0002
    R1). The leftover case — a `(1,1)` field made optional only by sitting in a
    Choice that did *not* become a oneof — has no proto hasbit, so `None` cannot
    round-trip; it is read directly.
    """
    proto_type = field_def.proto_type
    if isinstance(proto_type, xsd.TypeDefinition) and not isinstance(proto_type, xsd.Enumeration):
        return True  # Message-typed.
    if proto_type is xsd.AtomicType.DATE:
        return True  # google.protobuf.Timestamp.
    return in_oneof or field_def.computed_occurs[0] == 0


class PydanticConverterGenerator:
    def __init__(self, proto_module: str, models_module: str) -> None:
        self._proto_pkg, _, self._proto_mod = proto_module.rpartition(".")
        self._models_pkg, _, self._models_mod = models_module.rpartition(".")

    def _proto_ref(self, path: tuple[str, ...]) -> str:
        return f"{self._proto_mod}." + ".".join(path)

    def _models_ref(self, type_def: xsd.TypeDefinition) -> str:
        return f"{self._models_mod}.{_type_name(type_def)}"

    def header(self) -> Iterable[str]:
        for pkg, mod in ((self._proto_pkg, self._proto_mod), (self._models_pkg, self._models_mod)):
            yield f"from {pkg} import {mod}" if pkg else f"import {mod}"

    def footer(self) -> Iterable[str]:
        return []

    def _from_value_expr(self, proto_type: xsd.TypeDefinition | xsd.AtomicType, acc: str) -> str:
        """A `proto -> pydantic` expression for a single (non-collection) value."""
        if isinstance(proto_type, xsd.AtomicType):
            return f"{acc}.ToDatetime()" if proto_type is xsd.AtomicType.DATE else acc
        if isinstance(proto_type, xsd.Enumeration):
            return f"{self._models_ref(proto_type)}[{self._proto_ref(proto_type.path)}.Name({acc})]"
        return f"{_type_name(proto_type)}_from_proto({acc})"

    def _from_field(self, field_def: xsd.Field, kind: str, *, has_presence: bool) -> str:
        """The `proto -> pydantic` value expression for a whole field."""
        name = _field_name(field_def)
        acc = _pget(name)
        proto_type = field_def.proto_type
        match kind:
            case "map":
                if isinstance(proto_type, xsd.MapType) and proto_type.value_type is xsd.AtomicType.DATE:
                    return f"{{k: v.ToDatetime() for k, v in {acc}.items()}}"
                return f"dict({acc})"
            case "repeated":
                if isinstance(proto_type, xsd.AtomicType) and proto_type is not xsd.AtomicType.DATE:
                    return f"list({acc})"
                return f"[{self._from_value_expr(proto_type, 'v')} for v in {acc}]"
            case "optional":
                base = self._from_value_expr(proto_type, acc)
                return f"{base} if proto.HasField({name!r}) else None" if has_presence else base
            case _:  # required
                return self._from_value_expr(proto_type, acc)

    def _to_field(self, field_def: xsd.Field, kind: str) -> list[str]:
        """The `pydantic -> proto` statement(s) for a whole field."""
        name = _field_name(field_def)
        attr = _attr_name(name)
        acc = _pget(name)
        proto_type = field_def.proto_type
        match kind:
            case "map":
                if isinstance(proto_type, xsd.MapType) and proto_type.value_type is xsd.AtomicType.DATE:
                    return [f"for k, v in model.{attr}.items():", f"    {acc}[k].FromDatetime(v)"]
                return [f"{acc}.update(model.{attr})"]
            case "repeated":
                return self._to_repeated(field_def, acc, attr)
            case _:  # optional / required
                stmt = self._to_singular_stmt(field_def, name, attr)
                if kind == "optional":
                    return [f"if model.{attr} is not None:", f"    {stmt}"]
                return [stmt]

    def _to_repeated(self, field_def: xsd.Field, acc: str, attr: str) -> list[str]:
        proto_type = field_def.proto_type
        if proto_type is xsd.AtomicType.DATE:
            return [f"for v in model.{attr}:", f"    {acc}.add().FromDatetime(v)"]
        if isinstance(proto_type, xsd.AtomicType):
            return [f"{acc}.extend(model.{attr})"]
        if isinstance(proto_type, xsd.Enumeration):
            return [f"{acc}.extend({self._proto_ref(proto_type.path)}.Value(v.name) for v in model.{attr})"]
        return [f"{acc}.extend({_type_name(proto_type)}_to_proto(v) for v in model.{attr})"]

    def _to_singular_stmt(self, field_def: xsd.Field, name: str, attr: str) -> str:
        acc = _pget(name)
        proto_type = field_def.proto_type
        if proto_type is xsd.AtomicType.DATE:
            return f"{acc}.FromDatetime(model.{attr})"
        if isinstance(proto_type, xsd.Enumeration):
            return _pset(name, f"{self._proto_ref(proto_type.path)}.Value(model.{attr}.name)")
        if isinstance(proto_type, xsd.AtomicType):
            return _pset(name, f"model.{attr}")
        return f"{acc}.CopyFrom({_type_name(proto_type)}_to_proto(model.{attr}))"

    def from_proto(self, msg_def: xsd.Message, records: list[tuple[xsd.Field, bool, bool]]) -> Iterable[str]:
        type_name = _type_name(msg_def)
        yield f"def {type_name}_from_proto(proto):"
        if not records:
            yield f"    return {self._models_ref(msg_def)}()"
            return
        yield f"    return {self._models_ref(msg_def)}("
        for field_def, in_choice, in_oneof in records:
            kind = _field_kind(field_def, in_choice=in_choice)
            has_presence = _has_presence(field_def, in_oneof=in_oneof)
            expr = self._from_field(field_def, kind, has_presence=has_presence)
            yield f"        {_attr_name(field_def.name)}={expr},"
        yield "    )"

    def to_proto(
        self,
        msg_def: xsd.Message,
        records: list[tuple[xsd.Field, bool, bool]],
        oneof_groups: list[list[xsd.Field]],
    ) -> Iterable[str]:
        type_name = _type_name(msg_def)
        yield f"def {type_name}_to_proto(model):"
        yield f"    proto = {self._proto_ref(msg_def.path)}()"
        for group in oneof_groups:
            attrs = [_attr_name(f.name) for f in group]
            # Diagnose with the proto/XML field names, not the keyword-aliased
            # attribute names (`class_`): the user sees the former in JSON/proto.
            joined = ", ".join(_field_name(f) for f in group)
            args = ", ".join(f"model.{a}" for a in attrs)
            yield f"    if sum(x is not None for x in ({args})) > 1:"
            yield f'        raise ValueError("at most one of {joined} may be set in {type_name}")'
        for field_def, in_choice, _ in records:
            kind = _field_kind(field_def, in_choice=in_choice)
            yield from text.indent(self._to_field(field_def, kind), indent="    ")
        yield "    return proto"

    def message(self, msg_def: xsd.Message) -> Iterable[str]:
        records, oneof_groups = _leaf_records(msg_def)
        yield from self.from_proto(msg_def, records)
        yield ""
        yield ""
        yield from self.to_proto(msg_def, records, oneof_groups)


def generate(
    namespace: str,
    type_defs: tuple[xsd.TypeDefinition, ...],
    proto_module: str,
    models_module: str = "models",
) -> Iterator[str]:
    # Like the models module, the converter is module-scoped: every hoisted type
    # (including enclosed ones) gets a pair of functions, so all messages are
    # emitted regardless of nesting. Enums/maps need none — they convert inline.
    del namespace
    gen = PydanticConverterGenerator(proto_module, models_module)
    yield from gen.header()
    for type_def in type_defs:
        if not isinstance(type_def, xsd.Message):
            continue
        yield ""
        yield ""
        yield from gen.message(type_def)
