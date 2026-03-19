"""Parses DTD files into the xsdformer IR."""

from __future__ import annotations

import io
import pathlib
from typing import IO, Any

from lxml import etree

from xsdformer.xsd import text, xsd

# lxml DTD types are Cython classes not exposed as module-level attributes.
# We use Any for these internal lxml types.
_ContentNode = Any  # etree._DTDElementContentDecl
_ElementDecl = Any  # etree._DTDElementDecl
_AttributeDecl = Any  # etree._DTDAttributeDecl


def _occur_to_ir(occur: str) -> xsd.Occurs:
    """Maps lxml DTD occurrence strings to IR occurrence tuples."""
    match occur:
        case "once":
            return (1, 1)
        case "opt":
            return (0, 1)
        case "mult":
            return (0, None)
        case "plus":
            return (1, None)
        case _:
            raise ValueError(f"Unknown occur value: {occur}")


def _flatten_content_tree(node: _ContentNode) -> list[_ContentNode]:
    """Flattens a right-leaning binary tree of same-type content model nodes.

    lxml represents (a, b, c, d) as seq(a, seq(b, seq(c, d))).
    This flattens it to [a, b, c, d] when intermediate nodes have occur=once
    and matching type.
    """
    node_type = node.type
    if node_type not in ("seq", "or"):
        return [node]

    result: list[_ContentNode] = []
    if node.left is not None:
        result.append(node.left)
    current = node.right
    while current is not None and current.type == node_type and current.occur == "once":
        if current.left is not None:
            result.append(current.left)
        current = current.right
    if current is not None:
        result.append(current)

    return result


def _process_content_node(
    node: _ContentNode,
    element_messages: dict[str, xsd.Message],
) -> xsd.FieldDefinition:
    """Converts a DTD content model node into an IR FieldDefinition."""
    node_type = node.type
    occurs = _occur_to_ir(node.occur)

    if node_type == "element":
        target = element_messages[node.name]
        return xsd.Elem(
            name=text.snake_case(node.name),
            source=xsd.XMLElemSource(elem=node.name),
            documentation=None,
            occurs=occurs,
            proto_type=target,
            default=None,
        )

    if node_type in ("seq", "or"):
        children = [c for c in _flatten_content_tree(node) if c.type != "pcdata"]
        if not children:
            raise ValueError(f"Empty {node_type} after filtering pcdata")

        content = tuple(_process_content_node(c, element_messages) for c in children)
        if node_type == "seq":
            return xsd.Seq(documentation=None, occurs=occurs, content=content)
        return xsd.Choice(documentation=None, occurs=occurs, content=content)

    raise ValueError(f"Unexpected content node type: {node_type}")


def _has_element_children(node: _ContentNode | None) -> bool:
    """Checks if a content model tree contains any element references."""
    if node is None:
        return False
    if node.type == "element":
        return True
    return _has_element_children(node.left) or _has_element_children(node.right)


def _collect_mixed_elements(node: _ContentNode | None) -> list[_ContentNode]:
    """Collects all element nodes from a mixed content model tree."""
    if node is None:
        return []
    if node.type == "element":
        return [node]
    if node.type == "pcdata":
        return []
    result: list[_ContentNode] = []
    result.extend(_collect_mixed_elements(node.left))
    result.extend(_collect_mixed_elements(node.right))
    return result


def _collect_referenced_names(node: _ContentNode | None) -> set[str]:
    """Collects all element names referenced in a content model tree."""
    if node is None:
        return set()
    names: set[str] = set()
    if node.type == "element" and node.name:
        names.add(node.name)
    names.update(_collect_referenced_names(node.left))
    names.update(_collect_referenced_names(node.right))
    return names


def _attr_type_from_dtd(
    attr: _AttributeDecl,
    message: xsd.Message,
    enum_registry: dict[frozenset[str], xsd.Enumeration],
) -> xsd.AtomicType | xsd.Enumeration:
    """Maps a DTD attribute type to an IR type."""
    match attr.type:
        case "cdata" | "nmtoken" | "nmtokens" | "idref" | "idrefs" | "entity" | "entities":
            return xsd.AtomicType.STRING
        case "id":
            return xsd.AtomicType.ID
        case "enumeration":
            values = tuple(attr.values())
            key = frozenset(values)
            if key in enum_registry:
                return enum_registry[key]
            enum = xsd.Enumeration(
                name=text.pascal_case(attr.name),
                documentation=None,
                enum_values=values,
            )
            enum.enclosing_type = (message, None)
            enum_registry[key] = enum
            return enum
        case _:
            return xsd.AtomicType.STRING


def _attr_occurs(attr: _AttributeDecl) -> xsd.Occurs:
    """Maps DTD attribute default to IR occurrence."""
    match attr.default:
        case "required" | "fixed":
            return (1, 1)
        case _:
            return (0, 1)


def _process_attributes(
    element: _ElementDecl,
    message: xsd.Message,
    enum_registry: dict[frozenset[str], xsd.Enumeration],
) -> list[xsd.Attr]:
    """Creates Attr IR nodes from DTD attribute declarations."""
    attrs = []
    for attr in element.iterattributes():
        proto_type = _attr_type_from_dtd(attr, message, enum_registry)
        attrs.append(
            xsd.Attr(
                name=text.snake_case(attr.name),
                source=xsd.XMLAttrSource(attr=attr.name),
                documentation=None,
                occurs=_attr_occurs(attr),
                proto_type=proto_type,
                default=attr.default_value,
            ),
        )
    return attrs


def _build_mixed_content(
    element: _ElementDecl,
    element_messages: dict[str, xsd.Message],
) -> list[xsd.FieldDefinition]:
    """Builds fields for mixed content elements."""
    fields: list[xsd.FieldDefinition] = [
        xsd.ValueElem(documentation=None, proto_type=xsd.AtomicType.STRING),
    ]
    if _has_element_children(element.content):
        for elem_node in _collect_mixed_elements(element.content):
            target = element_messages[elem_node.name]
            fields.append(
                xsd.Elem(
                    name=text.snake_case(elem_node.name),
                    source=xsd.XMLElemSource(elem=elem_node.name),
                    documentation=None,
                    occurs=(0, None),
                    proto_type=target,
                    default=None,
                ),
            )
    return fields


def _build_element_content(
    element: _ElementDecl,
    element_messages: dict[str, xsd.Message],
) -> list[xsd.FieldDefinition]:
    """Builds fields from a pure element content model."""
    content_node = element.content
    if content_node is None:
        return []
    field_def = _process_content_node(content_node, element_messages)
    # Unwrap a single top-level Seq(1,1) wrapper (the DTD content model root
    # is always wrapped in one), but don't recursively flatten inner
    # sequences -- they represent meaningful groups.
    if isinstance(field_def, xsd.Seq) and field_def.occurs == (1, 1):
        return list(field_def.content)
    return [field_def]


def _build_message_content(
    element: _ElementDecl,
    message: xsd.Message,
    element_messages: dict[str, xsd.Message],
    enum_registry: dict[frozenset[str], xsd.Enumeration],
) -> tuple[xsd.FieldDefinition, ...]:
    """Builds the full content tuple for a Message from a DTD element."""
    fields: list[xsd.FieldDefinition] = _process_attributes(element, message, enum_registry)

    match element.type:
        case "empty":
            pass
        case "any":
            fields.append(xsd.ValueElem(documentation=None, proto_type=xsd.AtomicType.COMPLEXANY))
        case "mixed":
            fields.extend(_build_mixed_content(element, element_messages))
        case "element":
            fields.extend(_build_element_content(element, element_messages))

    return tuple(fields)


def _merge_occurs(a: xsd.Occurs, b: xsd.Occurs) -> xsd.Occurs:
    """Merges two occurrence tuples by taking the widest range."""
    min_o = min(a[0], b[0])
    max_o = None if a[1] is None or b[1] is None else max(a[1], b[1])
    return (min_o, max_o)


def _number_fields(message: xsd.Message) -> None:
    """Assigns field numbers and computed occurs to a Message's fields.

    Handles duplicate field names (from DTD choice branches with overlapping
    elements) by assigning the same field number and merging occurs.
    """
    seen: dict[str, xsd.Field] = {}
    next_num = 1
    for f, occurs in xsd.get_fields_occurs(message, occurs=(1, 1)):
        if f.name in seen:
            # Duplicate field: reuse number and widen occurs.
            first = seen[f.name]
            f.num = first.num
            f.computed_occurs = occurs
            first.computed_occurs = _merge_occurs(first.computed_occurs, occurs)
        else:
            f.num = next_num
            f.computed_occurs = occurs
            seen[f.name] = f
            next_num += 1


def _apply_map_overrides(
    all_types: list[xsd.TypeDefinition],
    config: xsd.Config,
) -> None:
    """Applies map override config to rewrite Message types as MapTypes."""
    rewriter = xsd.TypeRewriter(all_types)
    path_to_type = {t.path: t for t in all_types}

    for map_override in config.map_overrides:
        if map_type := path_to_type.get(map_override.map_type):
            key_field, val_field = xsd.find_map_fields(map_type, map_override)

            if not isinstance(key_field.proto_type, xsd.AtomicType):
                raise RuntimeError(f"expected map key: {key_field} to have an atomic type")
            if not isinstance(val_field.proto_type, xsd.AtomicType):
                raise RuntimeError(f"expected map value: {val_field} to have an atomic type")

            new_type_def = xsd.MapType(
                documentation=map_type.documentation,
                name=map_type.name,
                enclosing_type=map_type.enclosing_type,
                key_type=key_field.proto_type,
                value_type=val_field.proto_type,
                key_source=key_field.get_source(),
                value_source=val_field.get_source(),
            )
            rewriter.rewrite(map_type, new_type_def)

            for i, t in enumerate(all_types):
                if t is map_type:
                    all_types[i] = new_type_def
                    break


def _parse_dtd(
    dtd_file: str | bytes | pathlib.Path | IO[str] | IO[bytes],
) -> etree.DTD:
    """Parses a DTD from a file path or file-like object."""
    if isinstance(dtd_file, str | pathlib.Path):
        return etree.DTD(str(dtd_file))
    if isinstance(dtd_file, io.TextIOWrapper | io.StringIO):
        return etree.DTD(io.StringIO(dtd_file.read()))
    return etree.DTD(dtd_file)


def process_dtd(
    dtd_file: str | bytes | pathlib.Path | IO[str] | IO[bytes],
    config: xsd.Config | None = None,
) -> tuple[xsd.TypeDefinition, ...]:
    """Parses a DTD file and returns IR type definitions.

    Args:
        dtd_file: Path to a DTD file or a file-like object.
        config: Optional configuration for map overrides.

    Returns:
        A tuple of TypeDefinition objects (Messages and Enumerations).
    """
    dtd = _parse_dtd(dtd_file)
    if config is None:
        config = xsd.Config()

    elements = sorted(dtd.iterelements(), key=lambda e: e.name)

    # Pass 1: pre-create empty Messages for all elements.
    element_messages: dict[str, xsd.Message] = {}
    for element in elements:
        element_messages[element.name] = xsd.Message(
            name=text.pascal_case(element.name),
            documentation=None,
            content=(),
        )

    # Create stub Messages for elements referenced in content models but not
    # declared (e.g. MathML elements when the MathML DTD wasn't resolved).
    for element in elements:
        for name in _collect_referenced_names(element.content):
            if name not in element_messages:
                stub = xsd.Message(
                    name=text.pascal_case(name),
                    documentation=None,
                    content=(xsd.ValueElem(documentation=None, proto_type=xsd.AtomicType.COMPLEXANY),),
                )
                _number_fields(stub)
                element_messages[name] = stub

    # Pass 2: fill in content.
    enum_registry: dict[frozenset[str], xsd.Enumeration] = {}
    for element in elements:
        msg = element_messages[element.name]
        content = _build_message_content(element, msg, element_messages, enum_registry)
        object.__setattr__(msg, "content", content)
        _number_fields(msg)

    # Collect all type definitions: messages + enumerations.
    all_types: list[xsd.TypeDefinition] = list(element_messages.values())
    seen_enums: set[int] = set()
    for enum in enum_registry.values():
        if id(enum) not in seen_enums:
            all_types.append(enum)
            seen_enums.add(id(enum))

    if config.map_overrides:
        _apply_map_overrides(all_types, config)

    return tuple(all_types)
