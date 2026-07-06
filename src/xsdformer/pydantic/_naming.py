"""Shared naming rules for the pydantic models and the proto<->pydantic converter.

The models emitter (`generator.py`) and the converter emitter (`converter.py`)
must agree exactly on two naming decisions — how nested types are hoisted to
module scope, and how field names that collide with Python keywords are aliased.
That agreement is the ADR 0002 correctness premise (the converter constructs the
very models the generator emits), so the rules live here as the single source of
truth rather than the converter reaching into the generator's internals.
"""

import keyword

from xsdformer.xsd import xsd


def type_name(type_def: xsd.TypeDefinition) -> str:
    """The hoisted module-scope name for a type definition.

    Nested (enclosed) types are hoisted to module scope as `Parent_Child` — the
    PascalCase path components joined by `_`. Top-level types have a one-element
    path, so this is just their name.
    """
    return '_'.join(type_def.path)


def attr_name(name: str | None) -> str | None:
    """The attribute name for a field, suffixed if it collides with a keyword."""
    if name is not None and keyword.iskeyword(name):
        return name + '_'
    return name
