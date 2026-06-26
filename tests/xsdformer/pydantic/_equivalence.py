"""Semantic JSON-Schema equivalence for the pydantic gate (ADR 0002 slice 5).

The gate asserts that the IR-generated pydantic models (ADR 0002 slice 1) are
**semantically equivalent** to the contract — the same IR rendered as default-mode
TypeSpec and compiled by ``@typespec/json-schema``. Both sides are reduced to a
common ``$defs`` map and canonicalized so that *cosmetic* differences (which the
governing constraint tolerates) cancel, leaving only the wire/semantic essentials:
fields, resolved types, optionality, and enum member/value sets.

This is deliberately not a byte-diff: themis's freshness gate compares one
generator against itself, ours compares two different generators, which are never
byte-identical (ADR 0002 "Equivalence gate"). The canonicalization that makes a
pydantic-induced schema comparable to a TypeSpec bundle is exactly the type
normalization (``Optional[T]`` ≡ ``T | None``, default forms, integer widths,
required-but-empty arrays) that runtime/JSON-Schema give us for free — the reason
the ADR rejects an AST diff.

Canonicalization rules:

- **Refs:** the bundle writes ``$id``-relative refs (``"Role.json"``); rewrite to
  local ``"#/$defs/Role"`` to match pydantic (TypeSpec Discussion #4084).
- **Cosmetic keys dropped everywhere:** ``title``/``description`` (the dialect
  carries no field docs; the gate is structural), ``default`` (``Field(default=…)``
  ≡ bare default), per-``$def`` ``$id``/``$schema``.
- **Integer/number width dropped:** ``format``/``minimum``/``maximum``/… — the
  dialect maps every ``int8…uint64`` to Python ``int`` (and ``float32``/``float64``
  to ``float``), so the bundle's ``int32`` bounds have no pydantic counterpart.
  ``bytes`` encoding keys (``contentEncoding``/``contentMediaType``) likewise.
- **Nullable unwrapped:** pydantic renders ``T | None`` as
  ``anyOf: [T, {type: null}]``; TypeSpec marks an optional property by omission
  from ``required`` with a bare type. Unwrap the two-arm null union so both reduce
  to the bare ``T``; the optionality itself is carried by ``required``.
- **Collections excluded from ``required``:** a repeated field is ``T[]`` (required
  but possibly empty) in TypeSpec but ``list[T] = []`` (defaulted, not required) in
  pydantic; an empty list ≡ absent, so requiredness is not semantically meaningful
  for arrays/maps. Both sides drop array- and map-typed fields from ``required``.
"""

from __future__ import annotations

import copy
import enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, TypeAdapter

if TYPE_CHECKING:
    import types

_DROP_KEYS = frozenset(
    {
        "title",
        "description",
        "default",
        "$schema",
        "$id",
        "$comment",
        "format",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "contentEncoding",
        "contentMediaType",
    },
)


def tsp_defs(bundle: dict[str, Any]) -> dict[str, Any]:
    """The ``$defs`` map from a ``@typespec/json-schema`` bundle, refs localized."""
    defs = bundle.get("$defs")
    assert isinstance(defs, dict), "bundle has no $defs (not an emitAllModels bundle)"
    return copy.deepcopy(defs)


def pydantic_defs(module: types.ModuleType) -> dict[str, Any]:
    """A ``$defs``-style map built from every model/enum declared in ``module``.

    Each ``BaseModel`` contributes its root schema (its referenced ``$defs`` are
    dropped — they reappear as their own top-level entries); each ``Enum``
    contributes its ``TypeAdapter`` schema. Enumerating the module directly (rather
    than walking refs) captures unreferenced types too, matching the bundle's
    ``emitAllModels``.
    """
    own = [
        obj
        for obj in vars(module).values()
        # Only classes generated into this module — not imported bases (`Enum`,
        # `BaseModel`) that also live in the module namespace.
        if isinstance(obj, type) and obj.__module__ == module.__name__
    ]
    # Resolve forward references first: hoisted nested types reference each other
    # in declaration-independent order, so a model may not be fully defined until
    # every sibling exists. Rebuild all before inducing any schema.
    for obj in own:
        if issubclass(obj, BaseModel):
            obj.model_rebuild()

    defs: dict[str, Any] = {}
    for obj in own:
        name = obj.__name__
        if issubclass(obj, BaseModel):
            schema = obj.model_json_schema(ref_template="#/$defs/{model}")
            nested = schema.pop("$defs", {})
            # A recursive model roots a `$ref` to its own definition under `$defs`
            # (rather than inlining); pull that definition up as the entry.
            ref = schema.get("$ref")
            defs[name] = nested[ref.rsplit("/", 1)[-1]] if ref else schema
        elif issubclass(obj, enum.Enum):
            defs[name] = TypeAdapter(obj).json_schema()
    return defs


def _is_collection(node: Any) -> bool:  # noqa: ANN401
    """Whether a (canonicalized) property schema is a list or a map."""
    if not isinstance(node, dict):
        return False
    return node.get("type") == "array" or (node.get("type") == "object" and "additionalProperties" in node)


def _localize_ref(ref: str) -> str:
    """Rewrite a ``$id``-relative bundle ref to a local ``#/$defs/…`` pointer."""
    if ref.startswith("#"):
        return ref
    for suffix in (".json", ".yaml"):
        if ref.endswith(suffix):
            return f"#/$defs/{ref[: -len(suffix)]}"
    return ref


def _canon(node: Any) -> Any:  # noqa: ANN401, C901, PLR0912
    """Recursively canonicalize a schema node (returns a new structure)."""
    if isinstance(node, list):
        return [_canon(item) for item in node]
    if not isinstance(node, dict):
        return node

    # Unwrap a two-arm nullable union (`anyOf: [T, {type: null}]`, any order) to
    # the bare `T`; optionality is carried by the parent's `required` set.
    for combinator in ("anyOf", "oneOf"):
        arms = node.get(combinator)
        if isinstance(arms, list) and len(arms) == 2:
            non_null = [a for a in arms if not (isinstance(a, dict) and a.get("type") == "null")]
            if len(non_null) == 1:
                merged = {k: v for k, v in node.items() if k != combinator}
                merged.update(non_null[0])
                return _canon(merged)

    result: dict[str, Any] = {}
    for key, value in node.items():
        if key in _DROP_KEYS:
            continue
        if key == "$ref" and isinstance(value, str):
            result[key] = _localize_ref(value)
        elif key == "properties" and isinstance(value, dict):
            result[key] = {pname: _canon(pval) for pname, pval in value.items()}
        elif key == "required":
            # Deferred: recomputed below once properties are canonicalized.
            result[key] = value
        else:
            result[key] = _canon(value)

    # Drop array-/map-typed fields from `required`: a possibly-empty collection is
    # absent-equivalent, so its requiredness is not a semantic distinction.
    if isinstance(result.get("required"), list) and isinstance(result.get("properties"), dict):
        props = result["properties"]
        kept = sorted(name for name in result["required"] if not _is_collection(props.get(name)))
        if kept:
            result["required"] = kept
        else:
            del result["required"]

    return result


def canonicalize(defs: dict[str, Any]) -> dict[str, Any]:
    """Reduce a ``$defs`` map to its wire/semantic essentials for comparison."""
    return {name: _canon(schema) for name, schema in defs.items()}
