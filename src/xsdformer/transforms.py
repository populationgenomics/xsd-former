"""IR transforms that simplify parsed type definitions before code generation."""

from __future__ import annotations

import dataclasses
import enum
import pathlib
from collections.abc import Sequence
from typing import Any

import yaml

from xsdformer.xsd import xsd


class TransformHint(enum.Enum):
    DROPPED = 'dropped'
    INLINED_WRAPPER = 'inlined_wrapper'
    COLLAPSED_TO_STRING = 'collapsed_to_string'


@dataclasses.dataclass(frozen=True)
class InlinedWrapperInfo:
    """Extra info for INLINED_WRAPPER: the inner field source details."""

    inner_source: xsd.Source
    inner_proto_type: xsd.AtomicType


@dataclasses.dataclass(frozen=True)
class SerializeContentInfo:
    """Hint for serialized content: the ValueElem should call a named serializer."""

    serializer: str  # e.g. "markdown"


@dataclasses.dataclass(frozen=True)
class FlattenedListInfo:
    """Hint that this field was a list-wrapper flattened into the parent.

    inner_tag is the XML element tag of the items to collect from the wrapper.
    """

    inner_tag: str | None


@dataclasses.dataclass(frozen=True)
class CoercedToTimestampInfo:
    """Hint that this field was a date message coerced to google.protobuf.Timestamp."""


@dataclasses.dataclass(frozen=True)
class MapFieldConfig:
    """Config for converting a message type to a proto map<key, value>."""

    key: str  # field name of the map key within the inner message
    value: str  # field name of the map value within the inner message


@dataclasses.dataclass(frozen=True)
class Author:
    """A `[project] authors` entry."""

    name: str
    email: str | None = None


def _resolve_asset(config_path: pathlib.Path, value: str | None) -> pathlib.Path | None:
    """Resolves a build-config asset path (readme/license file) relative to the config file."""
    return (config_path.parent / value).resolve() if value else None


def _build_str(build: dict[str, Any], key: str) -> str | None:
    """Reads a string-valued `build:` key, rejecting YAML types that resemble one.

    An unquoted `6.30` is a YAML float and an unquoted `6` an int, so a
    version-like value silently loses information unless the config quotes it.

    Args:
        build: The `build:` mapping.
        key: Key to read.

    Returns:
        The string value, or None when absent or null.

    Raises:
        ValueError: If the value is present and not a string.
    """
    value = build.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f'build.{key} must be a string, got {type(value).__name__} ({value!r}). '
            'Quote it in the config; YAML reads an unquoted 6.30 as the number 6.3, '
            'so the original text is not recoverable here.',
        )
    return value


def _build_str_or(build: dict[str, Any], key: str, default: str) -> str:
    """Reads a string-valued `build:` key that has a non-null default."""
    value = _build_str(build, key)
    return default if value is None else value


def _required_build_str(build: dict[str, Any], key: str) -> str:
    """Reads a required string-valued `build:` key.

    Raises:
        ValueError: If the key is absent, null, or not a string.
    """
    value = _build_str(build, key)
    if value is None:
        raise ValueError(f'build.{key} is required.')
    return value


def _build_str_list(build: dict[str, Any], key: str) -> tuple[str, ...]:
    """Reads a list-of-strings `build:` key.

    A bare string is rejected rather than iterated: `dependencies: defusedxml`
    would otherwise yield one requirement per character, each a valid
    distribution name, and land in the published metadata.

    Raises:
        ValueError: If the value is not a list, or any entry is not a string.
    """
    value = build.get(key)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(
            f'build.{key} must be a list of strings, got {type(value).__name__} ({value!r}).',
        )
    for entry in value:
        if not isinstance(entry, str):
            raise ValueError(
                f'build.{key} entries must be strings, got {type(entry).__name__} ({entry!r}).',
            )
    return tuple(value)


def _build_mapping(build: dict[str, Any], key: str) -> dict[str, Any]:
    """Reads a mapping-valued `build:` key.

    Raises:
        ValueError: If the value is not a mapping.
    """
    value = build.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(
            f'build.{key} must be a mapping, got {type(value).__name__} ({value!r}).',
        )
    return value


def _build_authors(build: dict[str, Any]) -> tuple[Author, ...]:
    """Reads the `build.authors` list.

    Raises:
        ValueError: If the value is not a list of mappings with a `name`.
    """
    value = build.get('authors')
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f'build.authors must be a list of mappings, got {type(value).__name__} ({value!r}).')
    authors = []
    for entry in value:
        if not isinstance(entry, dict) or 'name' not in entry:
            raise ValueError(f'build.authors entries must be mappings with a name, got {entry!r}.')
        authors.append(Author(name=entry['name'], email=entry.get('email')))
    return tuple(authors)


@dataclasses.dataclass(frozen=True, kw_only=True)
class BuildConfig:
    namespace: str
    package_name: str
    # PyPI/distribution name for the generated package. Distinct from
    # `package_name`, which names the importable module directory (and so cannot
    # contain hyphens). Defaults to `package_name` when unset.
    distribution_name: str | None = None
    version: str = '0.1.0'
    # Optional [project] metadata for the generated package. All unset by
    # default, preserving the minimal-metadata output.
    description: str | None = None
    license_expr: str | None = None  # SPDX expression, e.g. "MIT" (PEP 639).
    keywords: tuple[str, ...] = ()
    classifiers: tuple[str, ...] = ()
    authors: tuple[Author, ...] = ()
    urls: tuple[tuple[str, str], ...] = ()  # ordered (label, url) pairs.
    # Asset files copied into the generated tree and referenced from pyproject.
    # Resolved relative to the config file. Unset -> not emitted.
    readme: pathlib.Path | None = None
    license_file: pathlib.Path | None = None
    # Oldest protobuf runtime the generated package is declared to support. Unset
    # -> the floor is whatever gencode the build's protoc stamps.
    min_protobuf_runtime: str | None = None
    # Extra runtime requirements, on top of the protobuf and pydantic ones every
    # generated package declares.
    dependencies: tuple[str, ...] = ()

    @classmethod
    def from_yaml(cls, path: pathlib.Path) -> BuildConfig | None:
        """Loads BuildConfig from the `build:` section of a YAML file, or None if absent."""
        with open(path) as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
        build = data.get('build')
        if not build:
            return None
        return cls(
            namespace=_required_build_str(build, 'namespace'),
            package_name=_required_build_str(build, 'package_name'),
            distribution_name=_build_str(build, 'distribution_name'),
            version=_build_str_or(build, 'version', '0.1.0'),
            description=_build_str(build, 'description'),
            license_expr=_build_str(build, 'license'),
            keywords=_build_str_list(build, 'keywords'),
            classifiers=_build_str_list(build, 'classifiers'),
            authors=_build_authors(build),
            urls=tuple(_build_mapping(build, 'urls').items()),
            readme=_resolve_asset(path, _build_str(build, 'readme')),
            license_file=_resolve_asset(path, _build_str(build, 'license_file')),
            min_protobuf_runtime=_build_str(build, 'min_protobuf_runtime'),
            dependencies=_build_str_list(build, 'dependencies'),
        )


@dataclasses.dataclass(frozen=True, kw_only=True)
class TransformConfig:
    drop_types: frozenset[str] = frozenset()
    drop_fields: dict[str, frozenset[str]] = dataclasses.field(default_factory=dict)
    inline_wrappers: bool = False
    flatten_list_wrappers: bool = False
    collapse_to_string: frozenset[str] = frozenset()
    rename_types: dict[str, str] = dataclasses.field(default_factory=dict)
    rename_fields: dict[str, dict[str, str]] = dataclasses.field(default_factory=dict)
    serialize_content: dict[str, str] = dataclasses.field(default_factory=dict)
    coerce_to_bool: bool = False
    coerce_to_timestamp: frozenset[str] = frozenset()
    comments: dict[str, str] = dataclasses.field(default_factory=dict)
    maps: dict[str, MapFieldConfig] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: pathlib.Path) -> TransformConfig:
        """Loads a TransformConfig from a YAML file."""
        with open(path) as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}

        drop_fields = {k: frozenset(v) for k, v in data.get('drop_fields', {}).items()}
        rename_fields = {k: dict(v) for k, v in data.get('rename_fields', {}).items()}
        return cls(
            drop_types=frozenset(data.get('drop_types', [])),
            drop_fields=drop_fields,
            inline_wrappers=data.get('inline_wrappers', False),
            flatten_list_wrappers=data.get('flatten_list_wrappers', False),
            collapse_to_string=frozenset(data.get('collapse_to_string', [])),
            rename_types=dict(data.get('rename_types', {})),
            rename_fields=rename_fields,
            serialize_content=dict(data.get('serialize_content', {})),
            coerce_to_bool=data.get('coerce_to_bool', False),
            coerce_to_timestamp=frozenset(data.get('coerce_to_timestamp', [])),
            comments=dict(data.get('comments', {})),
            maps={
                type_name: MapFieldConfig(key=cfg['key'], value=cfg['value'])
                for type_name, cfg in data.get('maps', {}).items()
            },
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
    """Returns the single non-dropped field of a message, or None if it has != 1."""
    fields = [f for f in msg.get_fields() if f.transform_hint is not TransformHint.DROPPED]
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
            if ref_field.transform_hint is TransformHint.DROPPED:
                continue
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
        # Skip wrappers whose inner type is nested inside them — reparenting
        # can cause name collisions.
        if (
            isinstance(field.proto_type, xsd.TypeDefinition)
            and field.proto_type.enclosing_type
            and field.proto_type.enclosing_type[0] is type_def
        ):
            continue
        # Single repeated field wrapper. Flatten it.
        inner_source = field.get_source()
        inner_tag = inner_source.elem if isinstance(inner_source, xsd.XMLElemSource) else None
        hint = FlattenedListInfo(inner_tag=inner_tag)
        for ref_field in field_index.get(id(type_def), []):
            if ref_field.transform_hint is TransformHint.DROPPED:
                continue
            ref_field.proto_type = field.proto_type
            ref_field.computed_occurs = field.computed_occurs
            ref_field.transform_hint = hint
        to_remove.add(id(type_def))
    return [d for d in defs if id(d) not in to_remove]


def _apply_maps(
    defs: list[xsd.TypeDefinition],
    maps: dict[str, MapFieldConfig],
    field_index: dict[int, list[xsd.Field]],
) -> list[xsd.TypeDefinition]:
    """Convert message types listed in `maps` config to MapType."""
    to_remove: set[int] = set()
    for type_def in defs:
        if type_def.name not in maps:
            continue
        if not isinstance(type_def, xsd.Message):
            raise ValueError(f'maps: {type_def.name!r} is not a message type')
        cfg = maps[type_def.name]
        fields_by_name = {f.name: f for f in type_def.get_fields()}
        key_field = fields_by_name.get(cfg.key)
        val_field = fields_by_name.get(cfg.value)
        if key_field is None:
            raise ValueError(f'maps: key field {cfg.key!r} not found in {type_def.name!r}')
        if val_field is None:
            raise ValueError(f'maps: value field {cfg.value!r} not found in {type_def.name!r}')
        if not isinstance(key_field.proto_type, xsd.AtomicType):
            raise ValueError(
                f'maps: key field {cfg.key!r} in {type_def.name!r} must be an atomic type '
                f'(proto3 map keys cannot be enums or messages); got {key_field.proto_type!r}',
            )
        if not isinstance(val_field.proto_type, xsd.AtomicType):
            raise ValueError(
                f'maps: value field {cfg.value!r} in {type_def.name!r} must be an atomic type; '
                f'got {val_field.proto_type!r}',
            )
        map_type = xsd.MapType(
            documentation=type_def.documentation,
            name=type_def.name,
            enclosing_type=type_def.enclosing_type,
            key_type=key_field.proto_type,
            value_type=val_field.proto_type,
            key_source=key_field.get_source(),
            value_source=val_field.get_source(),
        )
        for ref_field in field_index.get(id(type_def), []):
            if ref_field.transform_hint is TransformHint.DROPPED:
                continue
            ref_field.proto_type = map_type
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


def _coerce_to_bool(
    defs: list[xsd.TypeDefinition],
    field_index: dict[int, list[xsd.Field]],
) -> list[xsd.TypeDefinition]:
    """Auto-detect Y/N enums and coerce referencing fields to bool."""
    to_remove: set[int] = set()
    for type_def in defs:
        if not isinstance(type_def, xsd.Enumeration):
            continue
        if set(type_def.enum_values) != {'Y', 'N'}:
            continue
        for ref_field in field_index.get(id(type_def), []):
            ref_field.proto_type = xsd.AtomicType.BOOL
        to_remove.add(id(type_def))
    return [d for d in defs if id(d) not in to_remove]


def _coerce_to_timestamp(
    defs: list[xsd.TypeDefinition],
    coerce_to_timestamp: frozenset[str],
    field_index: dict[int, list[xsd.Field]],
) -> list[xsd.TypeDefinition]:
    """Replace date message types with google.protobuf.Timestamp fields."""
    to_remove: set[int] = set()
    info = CoercedToTimestampInfo()
    for type_def in defs:
        if type_def.name not in coerce_to_timestamp:
            continue
        for ref_field in field_index.get(id(type_def), []):
            ref_field.proto_type = xsd.AtomicType.DATE
            ref_field.transform_hint = info
        to_remove.add(id(type_def))
    return [d for d in defs if id(d) not in to_remove]


def _serialize_content(
    defs: list[xsd.TypeDefinition],
    serialize_content: dict[str, str],
) -> None:
    """For targeted messages, mark the ValueElem for serialization and drop Elem fields."""
    for type_def in defs:
        if type_def.name not in serialize_content:
            continue
        if not isinstance(type_def, xsd.Message):
            continue
        serializer = serialize_content[type_def.name]
        info = SerializeContentInfo(serializer=serializer)
        for field in type_def.get_fields():
            if isinstance(field, xsd.ValueElem):
                field.transform_hint = info
            elif isinstance(field, xsd.Elem):
                field.transform_hint = TransformHint.DROPPED


def _add_comments(
    defs: list[xsd.TypeDefinition],
    comments: dict[str, str],
) -> None:
    """Set or append documentation on types and fields from config."""
    for type_def in defs:
        if type_def.name in comments:
            type_def.documentation = comments[type_def.name]
        for field in type_def.get_fields():
            key = f'{type_def.name}.{field.name}'
            if key in comments:
                field.documentation = comments[key]


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

    # 0. Comments (before renames so config uses original type/field names).
    _add_comments(result, config.comments)

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

    # 2b. Coerce Y/N enums to bool (before auto-detect; enums aren't affected by inline/flatten).
    if config.coerce_to_bool:
        result = _coerce_to_bool(result, field_index)
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

    # 3b. Coerce date messages to Timestamp (after flatten so wrappers like History are gone).
    if config.coerce_to_timestamp:
        result = _coerce_to_timestamp(result, config.coerce_to_timestamp, field_index)
        field_index = _build_field_index(result)

    # 3c. Convert message types to proto maps.
    if config.maps:
        result = _apply_maps(result, config.maps, field_index)
        field_index = _build_field_index(result)

    # 4. Explicit collapse.
    result = _collapse_to_string(result, config.collapse_to_string, field_index)

    # 4b. Serialize content (after collapse so it can override hints on already-collapsed fields).
    _serialize_content(result, config.serialize_content)

    # 5. Renumber fields.
    _renumber_fields(result)

    return tuple(result)
