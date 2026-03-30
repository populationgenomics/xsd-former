import collections
import datetime
import functools
import inspect
import itertools
import re
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from lxml import etree

from xsdformer import generator
from xsdformer.transforms import CoercedToTimestampInfo, InlinedWrapperInfo, SerializeContentInfo, TransformHint
from xsdformer.xsd import text, xsd


def _xml_as_str(val: etree._Element) -> str:
    return etree.tostring(val)


def _node_is(val: etree._Element, tag: str) -> bool:
    return val.tag == tag


def _consume(queue: collections.deque[etree._Element], tag: str) -> etree._Element:
    node = queue.popleft()
    if node.tag != tag:
        raise ValueError(f"Expected {tag}, but saw {node.tag=}")
    return node


def _consume_if(
    queue: collections.deque[etree._Element],
    tag: str,
) -> etree._Element | None:
    if not (queue and _node_is(queue[0], tag)):
        return None
    return _consume(queue, tag)


def _xml_bool(val: str) -> int:
    match val:
        case "0" | "false" | "N":
            return False
        case "1" | "true" | "Y":
            return True
        case _:
            raise ValueError(f"Invalid boolean value: {val}")


def _xml_date(val: str) -> datetime.datetime:
    if val.endswith("Z"):
        val = val[:-1] + "+00:00"
    if re.match(r"[+-]\d{2}:\d{2}$", val[-6:]):
        return datetime.datetime.strptime(val, "%Y-%m-%d%z")
    return datetime.datetime.strptime(val, "%Y-%m-%d")  # noqa: DTZ007


_MONTH_NAMES = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _find_child(element: etree._Element, *names: str) -> etree._Element | None:
    for name in names:
        el = element.find(name)
        if el is not None:
            return el
    return None


def _parse_date_element(element: etree._Element) -> datetime.datetime:
    year_el = _find_child(element, "Year", "year")
    if year_el is None:
        md = _find_child(element, "MedlineDate", "medline_date")
        if md is not None and md.text:
            m = re.match(r"(\d{4})", md.text)
            if m:
                return datetime.datetime(int(m.group(1)), 1, 1)  # noqa: DTZ001
        return datetime.datetime(1, 1, 1)  # noqa: DTZ001
    year = int(year_el.text)
    month_el = _find_child(element, "Month", "month")
    if month_el is not None and month_el.text:
        month_text = month_el.text.strip()
        try:
            month = int(month_text)
        except ValueError:
            month = _MONTH_NAMES.get(month_text.lower()[:3], 1)
    else:
        month = 1
    day_el = _find_child(element, "Day", "day")
    day = int(day_el.text) if day_el is not None and day_el.text else 1
    hour_el = _find_child(element, "Hour", "hour")
    hour = int(hour_el.text) if hour_el is not None and hour_el.text else 0
    minute_el = _find_child(element, "Minute", "minute")
    minute = int(minute_el.text) if minute_el is not None and minute_el.text else 0
    second_el = _find_child(element, "Second", "second")
    second = int(second_el.text) if second_el is not None and second_el.text else 0
    return datetime.datetime(year, month, day, hour, minute, second)  # noqa: DTZ001


def _serialize_markdown(element: etree._Element) -> str:
    tags = {
        "b": ("**", "**"),
        "B": ("**", "**"),
        "i": ("*", "*"),
        "I": ("*", "*"),
        "u": ("__", "__"),
        "U": ("__", "__"),
        "sup": ("^(", ")"),
        "Sup": ("^(", ")"),
        "sub": ("~(", ")"),
        "Sub": ("~(", ")"),
    }
    parts: list[str] = []

    def _walk(el: etree._Element) -> None:
        pair = tags.get(el.tag)
        if pair:
            parts.append(pair[0])
        if el.text:
            parts.append(el.text)
        for child in el:
            _walk(child)
        if pair:
            parts.append(pair[1])
        if el.tail:
            parts.append(el.tail)

    if element.text:
        parts.append(element.text)
    for child in element:
        _walk(child)
    return "".join(parts)


_PROTO_PY_CONVERTER_METHODS: tuple[Callable[..., Any], ...] = (
    _node_is,
    _consume,
    _consume_if,
    _xml_as_str,
    _xml_bool,
    _xml_date,
    _find_child,
    _parse_date_element,
    _serialize_markdown,
)

_PROTO_PY_CONVERTER_CONSTANTS: tuple[tuple[str, Any], ...] = (("_MONTH_NAMES", _MONTH_NAMES),)


def _make_atom_caster(atom_type: xsd.AtomicType, var: str) -> str:
    caster_map = {
        xsd.AtomicType.ID: "{0}",
        xsd.AtomicType.URI: "{0}",
        xsd.AtomicType.STRING: "{0}",
        xsd.AtomicType.INT8: "int({0})",
        xsd.AtomicType.UINT8: "int({0})",
        xsd.AtomicType.INT16: "int({0})",
        xsd.AtomicType.UINT16: "int({0})",
        xsd.AtomicType.INT32: "int({0})",
        xsd.AtomicType.UINT32: "int({0})",
        xsd.AtomicType.UINT64: "int({0})",
        xsd.AtomicType.INT64: "int({0})",
        xsd.AtomicType.FLOAT: "float({0})",
        xsd.AtomicType.DOUBLE: "float({0})",
        xsd.AtomicType.BOOL: "_xml_bool({0})",
        xsd.AtomicType.DATE: "_xml_date({0})",
        xsd.AtomicType.BYTES: "({0}).encode('utf-8')",
        xsd.AtomicType.SIMPLEANY: "{0}",
        xsd.AtomicType.COMPLEXANY: "{0}",
    }
    try:
        return caster_map[atom_type].format(var)
    except KeyError as e:
        raise NotImplementedError(f"Not implemented for {atom_type=}") from e


@functools.singledispatch
def _make_caster(value_type: xsd.TypeDefinition | xsd.AtomicType, var: str) -> str:
    raise NotImplementedError(f"{value_type=}")


@_make_caster.register
def _(value_type: xsd.AtomicType, var: str) -> str:
    return _make_atom_caster(value_type, var)


@_make_caster.register
def _(proto_type: xsd.Enumeration, var: str) -> str:
    method = _method_name(proto_type.path)
    return f"{method}({var})"


def _method_name(path: tuple[str, ...]) -> str:
    method = "_".join(path)
    if len(path) > 1:
        method = "_" + method
    return method


def _get_map_value(
    name: str,
    source: xsd.Source,
    proto_type: xsd.AtomicType | xsd.Enumeration,
) -> str:
    match source:
        case xsd.XMLAttrSource():
            return _make_caster(proto_type, f"{name}.attrib[{source.attr!r}]")
        case xsd.XMLElemTextSource():
            return _make_caster(proto_type, f"{name}.text.strip()")
        case _:
            raise NotImplementedError(f"Not implemented for {source=}")


@functools.singledispatch
def _handle_field_definition(field: xsd.FieldDefinition) -> Iterable[str]:
    raise NotImplementedError(f"{field=}")


@_handle_field_definition.register
def _(field: xsd.Attr) -> Iterable[str]:
    if field.is_repeated:
        raise RuntimeError(f"field {field}: attribute cannot repeat.")

    def _consume_attr_once() -> Iterable[str]:
        cast_value = _make_caster(
            field.proto_type,
            f"element.attrib[{field.source.attr!r}]",
        )
        yield f"proto.{field.name} = {cast_value}"

    if field.occurs[0] == 0:
        yield f"if {field.source.attr!r} in element.attrib:"
        yield from text.indent(_consume_attr_once())
    else:
        yield from _consume_attr_once()


def _make_message_consumer(
    field: xsd.Elem,
    proto_type: xsd.Message,
    val: str,
) -> Callable[[], Iterable[str]]:
    method_name = _method_name(proto_type.path)

    def _consume_elem_once() -> Iterable[str]:
        if field.is_repeated:
            yield f"proto.{field.name}.append({method_name}({val}))"
        else:
            yield f"_fill_{method_name}({val}, proto.{field.name})"

    return _consume_elem_once


def _make_map_consumer(
    field: xsd.Elem,
    proto_type: xsd.MapType,
    val: str,
) -> Callable[[], Iterable[str]]:
    def _consume_elem_once() -> Iterable[str]:
        k = _get_map_value("kv", proto_type.key_source, proto_type.key_type)
        v = _get_map_value("kv", proto_type.value_source, proto_type.value_type)
        yield f"kv = {val}"
        yield f"proto.{field.name}[{k}] = {v}"

    return _consume_elem_once


def _make_elem_consumer(
    field: xsd.Elem,
    val: str,
) -> Callable[[], Iterable[str]]:
    if isinstance(field.proto_type, xsd.Message):
        return _make_message_consumer(field, field.proto_type, val)

    if isinstance(field.proto_type, xsd.MapType):
        return _make_map_consumer(field, field.proto_type, val)

    if field.proto_type == xsd.AtomicType.COMPLEXANY:
        caster = f"_xml_as_str({val})"
    elif isinstance(field.proto_type, xsd.AtomicType | xsd.Enumeration):
        caster = _make_caster(field.proto_type, f"{val}.text")
    else:
        raise NotImplementedError(f"{field.proto_type=}")

    if field.is_repeated:

        def _consume_elem_once() -> Iterable[str]:
            yield f"proto.{field.name}.append({caster})"
    else:

        def _consume_elem_once() -> Iterable[str]:
            yield f"proto.{field.name} = {caster}"

    return _consume_elem_once


def _handle_inlined_wrapper_elem(
    field: xsd.Elem,
    info: InlinedWrapperInfo,
) -> Iterable[str]:
    """Emit code to consume wrapper element and extract value from inner child."""
    wrapper_tag = field.source.elem
    match info.inner_source:
        case xsd.XMLElemSource(elem=inner_tag):
            extract = f"wrapper.find({inner_tag!r}).text"
        case xsd.XMLElemTextSource():
            extract = "wrapper.text"
        case _:
            raise NotImplementedError(f"Unsupported inner source: {info.inner_source}")

    caster = _make_atom_caster(info.inner_proto_type, extract)

    def _consume_once() -> Iterable[str]:
        yield f"wrapper = _consume(children, {wrapper_tag!r})"
        if field.is_repeated:
            yield f"proto.{field.name}.append({caster})"
        else:
            yield f"proto.{field.name} = {caster}"

    match field.occurs:
        case (1, 1):
            yield from _consume_once()
        case (0, 1):
            yield f"if children and children[0].tag == {wrapper_tag!r}:"
            yield from text.indent(_consume_once())
        case _:
            yield f"while children and children[0].tag == {wrapper_tag!r}:"
            yield from text.indent(_consume_once())


def _handle_coerced_to_timestamp_elem(field: xsd.Elem) -> Iterable[str]:
    """Emit code to consume a date element and parse it into a Timestamp."""
    tag = field.source.elem

    def _consume_once() -> Iterable[str]:
        if field.is_repeated:
            yield f"proto.{field.name}.add().FromDatetime(_parse_date_element(_consume(children, {tag!r})))"
        else:
            yield f"proto.{field.name}.FromDatetime(_parse_date_element(_consume(children, {tag!r})))"

    match field.occurs:
        case (1, 1):
            yield from _consume_once()
        case (0, 1):
            yield f"if children and children[0].tag == {tag!r}:"
            yield from text.indent(_consume_once())
        case _:
            yield f"while children and children[0].tag == {tag!r}:"
            yield from text.indent(_consume_once())


def _handle_dropped_elem(field: xsd.Elem) -> Iterable[str]:
    """Emit code to consume and discard an element."""
    match field.occurs:
        case (1, 1):
            yield f"_consume(children, {field.source.elem!r})"
        case (0, 1):
            yield f"_consume_if(children, {field.source.elem!r})"
        case _:
            yield f"while children and children[0].tag == {field.source.elem!r}:"
            yield from text.indent([f"_consume(children, {field.source.elem!r})"])


def _handle_flattened_list_elem(field: xsd.Elem) -> Iterable[str]:
    """Emit code to consume a wrapper element and iterate its children."""
    wrapper_tag = field.source.elem
    inner_consumer = _make_elem_consumer(field, "inner_elem")

    def _body() -> Iterable[str]:
        yield f"wrapper = _consume(children, {wrapper_tag!r})"
        yield "for inner_elem in wrapper:"
        yield from text.indent(inner_consumer())

    match field.occurs:
        case (1, 1):
            yield from _body()
        case (0, 1):
            yield f"if children and children[0].tag == {wrapper_tag!r}:"
            yield from text.indent(_body())
        case _:
            yield f"while children and children[0].tag == {wrapper_tag!r}:"
            yield from text.indent(_body())


@_handle_field_definition.register
def _(field: xsd.Elem) -> Iterable[str]:
    if field.transform_hint is TransformHint.DROPPED:
        yield from _handle_dropped_elem(field)
        return

    if isinstance(field.transform_hint, CoercedToTimestampInfo):
        yield from _handle_coerced_to_timestamp_elem(field)
        return

    if isinstance(field.transform_hint, InlinedWrapperInfo):
        yield from _handle_inlined_wrapper_elem(field, field.transform_hint)
        return

    if field.transform_hint is TransformHint.FLATTENED_LIST:
        yield from _handle_flattened_list_elem(field)
        return

    val = f"_consume(children, {field.source.elem!r})"

    _consume_elem_once = _make_elem_consumer(field, val)

    match field.occurs:
        case (1, 1):
            yield from _consume_elem_once()
        case (0, 1):
            yield f"if children and children[0].tag == {field.source.elem!r}:"
            yield from text.indent(_consume_elem_once())
        case _:
            yield f"while children and children[0].tag == {field.source.elem!r}:"
            yield from text.indent(_consume_elem_once())


@_handle_field_definition.register
def _(field: xsd.ValueElem) -> Iterable[str]:
    if isinstance(field.transform_hint, SerializeContentInfo):
        serializer_fn = f"_serialize_{field.transform_hint.serializer}"
        yield f"proto.{field.name} = {serializer_fn}(element)"
        return

    caster = _make_caster(field.proto_type, "element.text")
    if field.is_repeated:

        def _consume_elem_once() -> Iterable[str]:
            yield f"proto.{field.name}.append({caster})"
    else:

        def _consume_elem_once() -> Iterable[str]:
            yield f"proto.{field.name} = {caster}"

    match field.occurs:
        case (1, 1):
            yield from _consume_elem_once()
        case (0, 1):
            yield "if element.text and element.text.strip():"
            yield from text.indent(_consume_elem_once())
        case _:
            raise ValueError("repeated ValueElem")


@_handle_field_definition.register
def _(field: xsd.Seq) -> Iterable[str]:
    body = itertools.chain.from_iterable(_handle_field_definition(f) for f in field.content)

    fst = xsd.first(field)
    fst.discard(None)

    match field.occurs:
        case (1, 1):
            yield from body
        case (0, 1):
            yield f"if children and children[0].tag in {fst!r}:"
            yield from text.indent(body)
        case _:
            yield f"while children and children[0].tag in {fst!r}:"
            yield from text.indent(body)


@_handle_field_definition.register
def _(field: xsd.Choice) -> Iterable[str]:
    def _consume_choice_once() -> Iterable[str]:
        inner_map = collections.defaultdict(set)
        for i, inner in enumerate(field.content):
            for tag in xsd.first(inner):
                inner_map[tag].add(i)
        inner_map.pop(None, None)
        if not all(len(v) == 1 for v in inner_map.values()):
            raise ValueError(f"first sets of {field} contents are not mutually exclusive")

        yield "match children[0].tag:"
        for tag, content_idx in sorted(inner_map.items()):
            yield f"  case {tag!r}:"
            yield from text.indent(
                _handle_field_definition(field.content[content_idx.pop()]),
                indent="    ",
            )

    body = _consume_choice_once()

    fst = xsd.first(field)
    fst.discard(None)

    match field.occurs:
        case (1, 1):
            yield from body
        case (0, 1):
            yield f"if children and children[0].tag in {fst!r}:"
            yield from text.indent(body)
        case _:
            yield f"while children and children[0].tag in {fst!r}:"
            yield from text.indent(body)


class PyXMLConverterGenerator:
    indent: str = "  "

    def __init__(self, module: str) -> None:
        self._package, _, self._module = module.rpartition(".")

    def header(self) -> Iterable[str]:
        if self._package:
            yield f"from {self._package} import {self._module}"
        else:
            yield f"import {self._module}"
        yield "import collections"
        yield "import datetime"
        yield "import re"
        yield "from lxml import etree"
        yield ""
        yield ""
        for name, value in _PROTO_PY_CONVERTER_CONSTANTS:
            yield f"{name} = {value!r}"
            yield ""
        for method in _PROTO_PY_CONVERTER_METHODS:
            yield from inspect.getsource(method).split("\n")

    def footer(self) -> Iterable[str]:
        return []

    def begin_namespace(self, namespace: str) -> Iterable[str]:
        del namespace
        return []

    def end_namespace(self, namespace: str) -> Iterable[str]:
        del namespace
        return []

    @functools.singledispatchmethod
    def _message_field(
        self,
        field_def: xsd.FieldDefinition,
        msg_def: xsd.Message,
    ) -> Iterable[str]:
        raise NotImplementedError(f"Not implemented for {field_def=}")

    @functools.singledispatchmethod
    def _definition(self, type_def: xsd.TypeDefinition) -> Iterable[str]:
        raise NotImplementedError(f"Not implemented for {type_def=}")

    @_definition.register
    def _(self, msg_def: xsd.MapType) -> Iterable[str]:
        del msg_def
        yield from iter([])

    @_definition.register
    def _(self, msg_def: xsd.Message) -> Iterable[str]:
        for defn in msg_def.inner_types():
            yield from self.definition(defn)
        msg_class = self._module + "." + ".".join(msg_def.path)
        method_name = _method_name(msg_def.path)
        yield f"""


def _fill_{method_name}(element: etree._Element, proto: {msg_class}):
  children = collections.deque(element)
"""
        for field_def in msg_def.content:
            yield from text.indent(_handle_field_definition(field_def))

        yield f"""


def {method_name}(element: etree._Element) -> {msg_class}:
  proto = {msg_class}()
  _fill_{method_name}(element, proto)
  return proto
"""

    @_definition.register
    def _(self, enum_def: xsd.Enumeration) -> Iterable[str]:
        enum_class = self._module + "." + ".".join(enum_def.path)
        yield ""
        yield ""
        yield f"def {_method_name(enum_def.path)}(value: str) -> int:"
        yield "  return {"
        field_iter = enum_def.field_iter()
        unspecified = next(field_iter)
        for field in field_iter:
            yield f"      {field.xml_value!r}: {enum_class}.{field.name},"
        yield f"  }}.get(value, {enum_class}.{unspecified.name})"
        yield ""
        yield ""
        yield f"def {_method_name(enum_def.path)}_value(enum_value: int) -> str:"
        yield "  return {"
        field_iter = enum_def.field_iter()
        unspecified = next(field_iter)
        for field in field_iter:
            yield f"      {enum_class}.{field.name}: {field.xml_value!r},"
        yield "  }.get(enum_value, '')"

    def definition(self, type_def: xsd.TypeDefinition) -> Iterable[str]:
        yield from self._definition(type_def)


def generate(
    namespace: str,
    type_defs: tuple[xsd.TypeDefinition, ...],
    module: str,
) -> Iterator[str]:
    yield from generator.generate_with(
        PyXMLConverterGenerator(module),
        namespace,
        type_defs,
    )
