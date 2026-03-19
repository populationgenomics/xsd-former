"""IR transforms that simplify parsed type definitions before code generation."""

from __future__ import annotations

import dataclasses
import enum
from typing import TYPE_CHECKING, Any

import yaml

from xsdformer.xsd import xsd

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Sequence


class TransformHint(enum.Enum):
    DROPPED = "dropped"
    INLINED_WRAPPER = "inlined_wrapper"
    FLATTENED_LIST = "flattened_list"
    COLLAPSED_TO_STRING = "collapsed_to_string"


@dataclasses.dataclass(frozen=True)
class InlinedWrapperInfo:
    """Extra info for INLINED_WRAPPER: the inner field source details."""

    inner_source: xsd.Source
    inner_proto_type: xsd.AtomicType


@dataclasses.dataclass(frozen=True, kw_only=True)
class TransformConfig:
    drop_types: frozenset[str] = frozenset()
    drop_fields: dict[str, frozenset[str]] = dataclasses.field(default_factory=dict)
    inline_wrappers: bool = False
    flatten_list_wrappers: bool = False
    collapse_to_string: frozenset[str] = frozenset()
    rename_types: dict[str, str] = dataclasses.field(default_factory=dict)
    rename_fields: dict[str, dict[str, str]] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: pathlib.Path) -> TransformConfig:
        """Loads a TransformConfig from a YAML file."""
        with open(path) as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}

        drop_fields = {k: frozenset(v) for k, v in data.get("drop_fields", {}).items()}
        rename_fields = {k: dict(v) for k, v in data.get("rename_fields", {}).items()}
        return cls(
            drop_types=frozenset(data.get("drop_types", [])),
            drop_fields=drop_fields,
            inline_wrappers=data.get("inline_wrappers", False),
            flatten_list_wrappers=data.get("flatten_list_wrappers", False),
            collapse_to_string=frozenset(data.get("collapse_to_string", [])),
            rename_types=dict(data.get("rename_types", {})),
            rename_fields=rename_fields,
        )


def _build_field_index(
    defs: Sequence[xsd.TypeDefinition],
) -> dict[int, list[xsd.Field]]:
    """Maps id(type_def) -> list of fields that reference it."""
    index: dict[int, list[xsd.Field]] = {}
    for type_def in defs:
        for field in type_def.get_fields():
            if isinstance(field.proto_type, xsd.TypeDefinition):
                index.setdefault(id(field.proto_type), []).append(field)
    return index


def _rename_types(
    defs: list[xsd.TypeDefinition],
    rename_types: dict[str, str],
) -> None:
    for type_def in defs:
        if type_def.name in rename_types:
            type_def.name = rename_types[type_def.name]


def _rename_fields(
    defs: list[xsd.TypeDefinition],
    rename_fields: dict[str, dict[str, str]],
) -> None:
    for type_def in defs:
        if type_def.name not in rename_fields:
            continue
        field_renames = rename_fields[type_def.name]
        for field in type_def.get_fields():
            if field.name in field_renames:
                field.name = field_renames[field.name]


def _drop_types(
    defs: list[xsd.TypeDefinition],
    drop_types: frozenset[str],
    field_index: dict[int, list[xsd.Field]],
) -> list[xsd.TypeDefinition]:
    to_drop: set[int] = set()
    for type_def in defs:
        if type_def.name in drop_types:
            to_drop.add(id(type_def))
            for field in field_index.get(id(type_def), []):
                field.transform_hint = TransformHint.DROPPED
    return [d for d in defs if id(d) not in to_drop]


def _drop_fields(
    defs: list[xsd.TypeDefinition],
    drop_fields: dict[str, frozenset[str]],
) -> None:
    for type_def in defs:
        if type_def.name not in drop_fields:
            continue
        field_names = drop_fields[type_def.name]
        for field in type_def.get_fields():
            if field.name in field_names:
                field.transform_hint = TransformHint.DROPPED


def _get_single_field(msg: xsd.Message) -> xsd.Field | None:
    """Returns the single field of a message, or None if it has != 1 fields."""
    fields = list(msg.get_fields())
    if len(fields) != 1:
        return None
    return fields[0]


def _inline_wrappers(
    defs: list[xsd.TypeDefinition],
    field_index: dict[int, list[xsd.Field]],
) -> list[xsd.TypeDefinition]:
    to_remove: set[int] = set()
    for type_def in defs:
        if not isinstance(type_def, xsd.Message):
            continue
        field = _get_single_field(type_def)
        if field is None:
            continue
        if not isinstance(field.proto_type, xsd.AtomicType):
            continue
        if field.is_repeated:
            continue
        # This is a single-atomic-field wrapper. Inline it.
        info = InlinedWrapperInfo(
            inner_source=field.get_source(),
            inner_proto_type=field.proto_type,
        )
        for ref_field in field_index.get(id(type_def), []):
            ref_field.proto_type = field.proto_type
            ref_field.transform_hint = info
        to_remove.add(id(type_def))
    return [d for d in defs if id(d) not in to_remove]


def _flatten_list_wrappers(
    defs: list[xsd.TypeDefinition],
    field_index: dict[int, list[xsd.Field]],
) -> list[xsd.TypeDefinition]:
    to_remove: set[int] = set()
    for type_def in defs:
        if not isinstance(type_def, xsd.Message):
            continue
        field = _get_single_field(type_def)
        if field is None:
            continue
        if not field.is_repeated:
            continue
        # Single repeated field wrapper. Flatten it.
        for ref_field in field_index.get(id(type_def), []):
            ref_field.proto_type = field.proto_type
            ref_field.computed_occurs = field.computed_occurs
            ref_field.transform_hint = TransformHint.FLATTENED_LIST
        to_remove.add(id(type_def))
    return [d for d in defs if id(d) not in to_remove]


def _collapse_to_string(
    defs: list[xsd.TypeDefinition],
    collapse_to_string: frozenset[str],
    field_index: dict[int, list[xsd.Field]],
) -> list[xsd.TypeDefinition]:
    to_remove: set[int] = set()
    for type_def in defs:
        if type_def.name not in collapse_to_string:
            continue
        for ref_field in field_index.get(id(type_def), []):
            ref_field.proto_type = xsd.AtomicType.COMPLEXANY
            ref_field.transform_hint = TransformHint.COLLAPSED_TO_STRING
        to_remove.add(id(type_def))
    return [d for d in defs if id(d) not in to_remove]


def _renumber_fields(defs: list[xsd.TypeDefinition]) -> None:
    for type_def in defs:
        if not isinstance(type_def, xsd.Message):
            continue
        i = 1
        for field in type_def.get_fields():
            if field.transform_hint is TransformHint.DROPPED:
                field.num = None
                continue
            field.num = i
            i += 1


def apply_transforms(
    defs: tuple[xsd.TypeDefinition, ...],
    config: TransformConfig,
) -> tuple[xsd.TypeDefinition, ...]:
    """Applies transforms to the IR in-place and returns the filtered defs."""
    result = list(defs)

    # 1. Renames first (before any removals).
    _rename_types(result, config.rename_types)
    _rename_fields(result, config.rename_fields)

    # Build field index after renames.
    field_index = _build_field_index(result)

    # 2. Drop types/fields.
    result = _drop_types(result, config.drop_types, field_index)
    _drop_fields(result, config.drop_fields)

    # Rebuild index after drops.
    field_index = _build_field_index(result)

    # 3. Auto-detect transforms (loop to handle cascading).
    changed = True
    while changed:
        prev_len = len(result)
        if config.inline_wrappers:
            result = _inline_wrappers(result, field_index)
            field_index = _build_field_index(result)
        if config.flatten_list_wrappers:
            result = _flatten_list_wrappers(result, field_index)
            field_index = _build_field_index(result)
        changed = len(result) < prev_len

    # 4. Explicit collapse.
    result = _collapse_to_string(result, config.collapse_to_string, field_index)

    # 5. Renumber fields.
    _renumber_fields(result)

    return tuple(result)
