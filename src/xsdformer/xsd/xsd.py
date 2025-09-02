import abc
import collections
import dataclasses
import enum
import functools
import graphlib
import itertools
import re
from collections.abc import Iterator, Sequence
from typing import Protocol, TypeAlias

import elementpath
import xmlschema
import xmlschema.aliases
import xmlschema.names

from xsdformer.xsd import text

Occurs: TypeAlias = tuple[int, int | None]
_XsdComplexType = xmlschema.validators.complex_types.XsdComplexType
_XsdSimpleType = xmlschema.validators.simple_types.XsdSimpleType
_BaseXsdType: TypeAlias = _XsdComplexType | _XsdSimpleType


def _remove_common_prefix(
  a: Sequence[str],
  b: Sequence[str],
) -> tuple[Sequence[str], Sequence[str]]:
  """Removes common prefix elements from two sequences of strings.

  Args:
    a: The first sequence of strings.
    b: The second sequence of strings.

  Returns:
    A tuple containing the two sequences with the common prefix removed.
  """
  while a and b and a[0] == b[0]:
    a = a[1:]
    b = b[1:]
  return a, b


def _relative_path(path: tuple[str, ...], relative_to: tuple[str, ...]) -> str:
  a, b = _remove_common_prefix(relative_to, path)
  if b[0] in a:
    return "." + ".".join(path)
  return ".".join(b)


def _is_repeated(occurs: Occurs) -> bool:
  return occurs[1] is None or occurs[1] > 1


def _get_comment(t: xmlschema.XsdComponent) -> str | None:
  """Extracts and normalizes documentation from an XSD type annotation."""
  if t.annotation is not None:
    return text.normalize_whitespace([e.text for e in t.annotation.documentation])
  return None


@dataclasses.dataclass(frozen=True, kw_only=True, eq=True)
class MapOverrideConfig:
  map_type: tuple[str, ...]
  key_field: str
  value_field: str


@dataclasses.dataclass(frozen=True, kw_only=True, eq=True)
class Config:
  map_overrides: Sequence[MapOverrideConfig] = ()


class AtomicType(enum.Enum):
  ID = enum.auto()
  URI = enum.auto()
  STRING = enum.auto()
  INT8 = enum.auto()
  UINT8 = enum.auto()
  INT16 = enum.auto()
  UINT16 = enum.auto()
  INT32 = enum.auto()
  UINT32 = enum.auto()
  UINT64 = enum.auto()
  INT64 = enum.auto()
  FLOAT = enum.auto()
  DOUBLE = enum.auto()
  BOOL = enum.auto()
  BYTES = enum.auto()
  DATE = enum.auto()
  SIMPLEANY = enum.auto()
  COMPLEXANY = enum.auto()

  @property
  def proto_str(self) -> str:
    return _PROTO_STR_LUT[self]


_PROTO_STR_LUT = {
  AtomicType.ID: "string",
  AtomicType.URI: "string",
  AtomicType.STRING: "string",
  AtomicType.INT8: "int8",
  AtomicType.UINT8: "uint8",
  AtomicType.INT16: "int16",
  AtomicType.UINT16: "uint16",
  AtomicType.INT32: "int32",
  AtomicType.UINT32: "uint32",
  AtomicType.UINT64: "uint64",
  AtomicType.INT64: "int64",
  AtomicType.FLOAT: "float",
  AtomicType.DOUBLE: "double",
  AtomicType.BOOL: "bool",
  AtomicType.BYTES: "bytes",
  AtomicType.DATE: "google.protobuf.Timestamp",
  AtomicType.SIMPLEANY: "string",
  AtomicType.COMPLEXANY: "string",
}


_PROTO_ATOMIC_TYPE = {
  elementpath.datatypes.proxies.StringProxy: AtomicType.STRING,
  elementpath.datatypes.string.Id: AtomicType.ID,
  elementpath.datatypes.uri.AnyURI: AtomicType.URI,
  elementpath.datatypes.numeric.Int: AtomicType.INT32,
  elementpath.datatypes.numeric.PositiveInteger: AtomicType.UINT64,
  elementpath.datatypes.numeric.NonNegativeInteger: AtomicType.UINT64,
  elementpath.datatypes.numeric.Integer: AtomicType.INT64,
  elementpath.datatypes.datetime.Date10: AtomicType.DATE,
  elementpath.datatypes.proxies.BooleanProxy: AtomicType.BOOL,
  elementpath.datatypes.proxies.DoubleProxy10: AtomicType.DOUBLE,
  elementpath.datatypes.proxies.DecimalProxy: AtomicType.DOUBLE,
}


def multiply_occurs(a: Occurs, b: Occurs) -> Occurs:
  min_occus = a[0] * b[0]
  if a[1] == 0 or b[1] == 0:
    max_occurs = 0
  elif a[1] is None or b[1] is None:
    max_occurs = None
  else:
    max_occurs = a[1] * b[1]
  return (min_occus, max_occurs)


@dataclasses.dataclass(frozen=True, kw_only=True)
class Source: ...


@dataclasses.dataclass(frozen=True, kw_only=True)
class XMLAttrSource(Source):
  attr: str


@dataclasses.dataclass(frozen=True, kw_only=True)
class XMLElemSource(Source):
  elem: str


@dataclasses.dataclass(frozen=True, kw_only=True)
class XMLElemTextSource(Source): ...


@dataclasses.dataclass(eq=False, kw_only=True)
class Definition:
  comment: str | None
  name: str | None

  def get_fields(self) -> Iterator["Field"]:
    for f, o in get_fields_occurs(self, occurs=(1, 1)):
      del o
      yield f


@dataclasses.dataclass(eq=False, kw_only=True)
class FieldDefinition(Definition, abc.ABC):
  occurs: Occurs


@dataclasses.dataclass(eq=False, kw_only=True)
class TypeDefinition(Definition):
  enclosing_type: tuple["TypeDefinition", "Field"] | None = None

  @property
  def path(self) -> tuple[str, ...]:
    if self.name is None:
      raise RuntimeError(f"{self}: anonymous type has not been assigned a name.")
    if not self.enclosing_type:
      return (self.name,)
    return (*self.enclosing_type[0].path, self.name)


@dataclasses.dataclass(eq=False, kw_only=True)
class Field(FieldDefinition, abc.ABC):
  proto_type: TypeDefinition | AtomicType
  _computed_occurs: Occurs | None = dataclasses.field(default=None, init=False)
  num: int | None = None

  @property
  def computed_occurs(self) -> Occurs:
    if self._computed_occurs is None:
      raise RuntimeError("{self}: field occurrence has not been computed.")
    return self._computed_occurs

  @computed_occurs.setter
  def computed_occurs(self, value: Occurs) -> None:
    self._computed_occurs = value

  @abc.abstractmethod
  def get_source(self) -> Source: ...

  @property
  def is_repeated(self) -> bool:
    return _is_repeated(self.computed_occurs)

  @abc.abstractmethod
  def proto_type_str(self, path: tuple[str, ...]) -> str: ...


def get_fields_occurs(
  defn: Definition,
  occurs: Occurs,
) -> Iterator[tuple[Field, Occurs]]:
  match defn:
    case Enumeration():
      pass
    case Message():
      yield from itertools.chain.from_iterable(
        get_fields_occurs(c, occurs=occurs) for c in defn.content
      )
    case FieldContainer():
      yield from itertools.chain.from_iterable(
        get_fields_occurs(c, occurs=multiply_occurs(occurs, defn.occurs))
        for c in defn.content
      )
    case Field():
      yield defn, multiply_occurs(occurs, defn.occurs)


@dataclasses.dataclass(eq=False, kw_only=True)
class FieldContainer(FieldDefinition):
  name: str | None = dataclasses.field(default=None, init=False)
  content: tuple[FieldDefinition, ...]

  def __post_init__(self) -> None:
    for field in self.content:
      if isinstance(field, Attr):
        raise ValueError("{self}: field containers can't contain attributes")


class Seq(FieldContainer): ...


class Choice(FieldContainer): ...


class HasSource(Protocol):
  source: Source


@dataclasses.dataclass(eq=False, kw_only=True)
class Elem(Field):
  """Represents an attribute or element definition within an XSD."""

  default: str | None
  source: XMLElemSource

  def get_source(self) -> Source:
    return self.source

  def __repr__(self) -> str:
    return f"Elem({self.name})"

  def proto_type_str(self, path: tuple[str, ...]) -> str:
    repeated = "repeated " if self.is_repeated else ""
    if isinstance(self.proto_type, AtomicType):
      return repeated + self.proto_type.proto_str
    if isinstance(self.proto_type, MapType):
      key_type = self.proto_type.key_type.proto_str
      val_type = self.proto_type.value_type.proto_str
      return f"map<{key_type}, {val_type}>"
    return repeated + _relative_path(self.proto_type.path, path)

  @property
  def is_complex_any(self) -> bool:
    return self.proto_type is AtomicType.COMPLEXANY


@dataclasses.dataclass(eq=False, kw_only=True)
class ValueElem(Field):
  """Represents an attribute or element definition within an XSD."""

  name: str | None = "value"
  default: str | None = None
  occurs: Occurs = (0, 1)

  def get_source(self) -> Source:
    return XMLElemTextSource()

  def __repr__(self) -> str:
    return f"ValueElem({self.name})"

  @Field.computed_occurs.setter
  def computed_occurs(self, value: Occurs) -> None:
    if _is_repeated(value):
      raise ValueError(f"{self}: element values cannot be repeated (occurs={value})")
    self._computed_occurs = value

  def proto_type_str(self, path: tuple[str, ...]) -> str:
    if isinstance(self.proto_type, AtomicType):
      return self.proto_type.proto_str
    return _relative_path(self.proto_type.path, path)

  @property
  def is_complex_any(self) -> bool:
    return self.proto_type is AtomicType.COMPLEXANY


@dataclasses.dataclass(eq=False, kw_only=True)
class Attr(Field):
  """Represents an attribute or element definition within an XSD."""

  default: str | None
  source: XMLAttrSource

  def get_source(self) -> Source:
    return self.source

  def __repr__(self) -> str:
    return f"Attr({self.name})"

  @Field.computed_occurs.setter
  def computed_occurs(self, value: Occurs) -> None:
    if _is_repeated(value):
      raise ValueError(f"{self}: attributes cannot be repeated (occurs={value})")
    self._computed_occurs = value

  def proto_type_str(self, path: tuple[str, ...]) -> str:
    if isinstance(self.proto_type, AtomicType):
      return self.proto_type.proto_str
    return _relative_path(self.proto_type.path, path)


@dataclasses.dataclass(eq=False, kw_only=True)
class Message(TypeDefinition):
  content: tuple[FieldDefinition, ...]

  def inner_types(self) -> Iterator[TypeDefinition]:
    for field in self.get_fields():
      if (
        isinstance(field.proto_type, TypeDefinition)
        and field.proto_type.enclosing_type
        and field.proto_type.enclosing_type[0] is self
      ):
        yield field.proto_type

  def __repr__(self) -> str:
    return f"Message({self.name})"


@dataclasses.dataclass(frozen=True)
class EnumField:
  num: int
  name: str
  xml_value: str | None


@dataclasses.dataclass(eq=False, kw_only=True)
class Enumeration(TypeDefinition):
  enum_values: tuple[str, ...]

  def __repr__(self) -> str:
    return f"Enumeration({self.name})"

  def field_iter(self) -> Iterator[EnumField]:
    if self.name is None:
      raise RuntimeError(f"Anonymous enumeration {self} has not been assigned a name.")

    base = text.snake_case(self.name).upper()

    yield EnumField(0, base + "_UNSPECIFIED", None)
    for i, xml_value in enumerate(self.enum_values, start=1):
      yield EnumField(i, base + "_" + text.snake_case(xml_value).upper(), xml_value)


@dataclasses.dataclass(eq=False, kw_only=True)
class MapType(TypeDefinition):
  key_type: AtomicType
  value_type: AtomicType
  key_source: Source
  value_source: Source

  def __repr__(self) -> str:
    return f"MapType({self.name})"


def first(field_def: FieldDefinition) -> set[str | None]:
  fst = set()

  match field_def:
    case Seq():
      for c in field_def.content:
        fst.update(first(c))
        if None not in fst:
          break
        fst.remove(None)
    case Choice():
      for c in field_def.content:
        fst.update(first(c))
    case Elem():
      fst.add(field_def.source.elem)

  if field_def.occurs[0] == 0:
    fst.add(None)
  return fst


def _get_proto_type(t: xmlschema.XsdType) -> AtomicType:
  if t.is_complex() and t.name == xmlschema.names.XSD_ANY_TYPE:
    return AtomicType.COMPLEXANY
  if t.is_simple() and t.name == xmlschema.names.XSD_ANY_SIMPLE_TYPE:
    return AtomicType.SIMPLEANY
  if t.is_union():
    return AtomicType.STRING
  if isinstance(t, xmlschema.validators.simple_types.XsdAtomicBuiltin):
    return _PROTO_ATOMIC_TYPE[t.simple_type.datatype]
  if t.simple_type and t.simple_type.base_type:
    return _get_proto_type(t.simple_type.base_type)
  raise NotImplementedError(f"not implemented: {t=}")


def _resolve_proto_type(
  t: xmlschema.XsdType,
  type_defs: dict[xmlschema.XsdType, TypeDefinition],
) -> TypeDefinition | AtomicType:
  try:
    return type_defs[t]
  except KeyError:
    return _get_proto_type(t)


def _generate_attributes(
  attributes: xmlschema.validators.attributes.XsdAttributeGroup,
  type_defs: dict[xmlschema.XsdType, TypeDefinition],
) -> Iterator[Attr]:
  for attr in attributes.values():
    if not isinstance(attr, xmlschema.validators.attributes.XsdAttribute):
      continue
    occurs = (0, 1) if getattr(attr, "use", "optional") == "optional" else (1, 1)

    yield Attr(
      name=text.snake_case(attr.name),
      source=XMLAttrSource(attr=attr.name),
      comment=_get_comment(attr),
      occurs=occurs,
      proto_type=_resolve_proto_type(attr.type, type_defs),
      default=attr.default,
    )


@functools.singledispatch
def _process_content(
  t: xmlschema.XsdType,
  type_defs: dict[xmlschema.XsdType, TypeDefinition],
) -> FieldDefinition:
  raise NotImplementedError(f"not implemented: {t=}")


@_process_content.register
def _(
  elem: xmlschema.validators.elements.XsdElement,
  type_defs: dict[xmlschema.XsdType, TypeDefinition],
) -> FieldDefinition:
  return Elem(
    name=text.snake_case(elem.name),
    source=XMLElemSource(elem=elem.name),
    comment=_get_comment(elem),
    occurs=(elem.min_occurs, elem.max_occurs),
    proto_type=_resolve_proto_type(elem.type, type_defs),
    default=elem.default,
  )


@_process_content.register
def _(
  t: xmlschema.validators.groups.XsdGroup,
  type_defs: dict[xmlschema.XsdType, TypeDefinition],
) -> FieldContainer:
  match t.model:
    case "sequence":
      return Seq(
        comment=_get_comment(t),
        occurs=(t.min_occurs, t.max_occurs),
        content=tuple(_process_content(elem, type_defs) for elem in t.content),
      )
    case "choice":
      return Choice(
        comment=_get_comment(t),
        occurs=(t.min_occurs, t.max_occurs),
        content=tuple(_process_content(elem, type_defs) for elem in t.content),
      )
    case model:
      raise NotImplementedError(f"not implemented: {model=}")


def _message_content(
  t: _XsdComplexType,
  type_defs: dict[xmlschema.XsdType, TypeDefinition],
) -> Iterator[FieldDefinition]:
  yield from _generate_attributes(t.attributes, type_defs)
  if isinstance(t.content, _XsdSimpleType):
    yield ValueElem(
      comment=_get_comment(t),
      proto_type=_resolve_proto_type(t.content, type_defs),
    )
  else:
    fields = _process_content(t.content, type_defs)
    fields = tuple(_flatten_simple_seqs(fields))
    yield from fields
    if t.mixed:
      if fields:
        raise NotImplementedError("Mixed content type with elements.")
      else:
        yield ValueElem(
          comment=_get_comment(t),
          proto_type=AtomicType.STRING,
        )


def _print_message_content(c: FieldDefinition, depth: int = 0) -> None:
  indent = "  " * depth
  match c:
    case Seq():
      print(f"{indent}{c.occurs} seq {{")
      for c2 in c.content:
        _print_message_content(c2, depth + 1)
      print(f"{indent}}}")
    case Choice():
      print(f"{indent}{c.occurs} choice {{")
      for c2 in c.content:
        _print_message_content(c2, depth + 1)
      print(f"{indent}}}")
    case Attr():
      print(f"{indent}{c.occurs} attr {c.name}")
    case Elem():
      print(f"{indent}{c.occurs} elem {c.name}")
    case _:
      raise NotImplementedError(f"Not implemented for {c=}")


def _print_message(message: Message) -> None:
  print(f"message: {message.name}")
  for c in message.content:
    _print_message_content(c)


def _get_type_name(t: xmlschema.XsdType) -> str | None:
  if t.name:
    return text.pascal_case(re.sub(r"^type", "", t.name))
  if t.parent is not None and t.parent.is_global():
    return text.pascal_case(t.parent.name)
  return None


def _flatten_simple_seqs(f: FieldDefinition) -> Iterator[FieldDefinition]:
  if isinstance(f, FieldContainer):
    content = []
    for c in f.content:
      content.extend(_flatten_simple_seqs(c))
    if isinstance(f, Seq) and f.occurs == (1, 1):
      yield from content
    else:
      yield dataclasses.replace(f, content=tuple(content))
  else:
    yield f


def _make_message_for(
  t: _XsdComplexType,
  type_defs: dict[xmlschema.XsdType, TypeDefinition],
) -> Message:
  content = tuple(_message_content(t, type_defs))

  message = Message(name=_get_type_name(t), comment=_get_comment(t), content=content)
  for i, f_occurs in enumerate(get_fields_occurs(message, occurs=(1, 1)), start=1):
    f, occurs = f_occurs
    f.num = i
    f.computed_occurs = occurs
    if isinstance(f.proto_type, Definition) and f.proto_type.name is None:
      f.proto_type.name = text.pascal_case(f.name)
      f.proto_type.enclosing_type = (message, f)
  return message


def _make_enum_for(t: _XsdSimpleType) -> Enumeration:
  if t.enumeration is None:
    raise ValueError(f"expected {t} to be an enumeration.")
  return Enumeration(
    name=_get_type_name(t),
    comment=_get_comment(t),
    enum_values=tuple(str(v) for v in t.enumeration),
  )


def _make_definition_for(
  t: xmlschema.XsdType,
  type_defs: dict[xmlschema.XsdType, TypeDefinition],
) -> TypeDefinition | None:
  match t:
    case _XsdSimpleType(
      name=xmlschema.names.XSD_ANY_SIMPLE_TYPE,
    ):
      return None
    case _XsdComplexType(
      name="{http://www.w3.org/2001/XMLSchema}anyType",
    ):
      return None
    case xmlschema.validators.XsdAtomicBuiltin():
      return None
    case xmlschema.validators.XsdUnion():
      return None
    case _XsdSimpleType(enumeration=e) if e is not None:
      return _make_enum_for(t)
    case _XsdComplexType():
      return _make_message_for(t, type_defs)
    case _:
      raise NotImplementedError(f"not implemented: {t=}")


def _include_type(xsd_type: xmlschema.XsdType | None) -> bool:
  def _is_any_type(xsd_type: xmlschema.XsdType) -> bool:
    match xsd_type:
      case _XsdComplexType(name=xmlschema.names.XSD_ANY_TYPE):
        return True
      case _XsdSimpleType(name=xmlschema.names.XSD_ANY_SIMPLE_TYPE):
        return True
      case _:
        return False

  def _is_enum(xsd_type: xmlschema.XsdType) -> bool:
    return bool(
      isinstance(xsd_type, xmlschema.validators.XsdAtomicRestriction)
      and xsd_type.enumeration is not None,
    )

  return not (
    xsd_type is None
    or (xsd_type.is_atomic() and not _is_enum(xsd_type))
    or _is_any_type(xsd_type)
  )


def _get_xsd_dependencies(component: xmlschema.XsdComponent) -> set[_BaseXsdType]:
  deps = set()

  match component:
    case xmlschema.validators.XsdUnion():
      for t in component.member_types:
        deps.update(_get_xsd_dependencies(t))
    case xmlschema.validators.elements.XsdElement():
      deps.update(_get_xsd_dependencies(component.type))
    case xmlschema.validators.attributes.XsdAttribute():
      deps.update(_get_xsd_dependencies(component.type))
    case xmlschema.validators.attributes.XsdAttributeGroup():
      for t in component.values():
        deps.update(_get_xsd_dependencies(t))
    case xmlschema.validators.groups.XsdGroup():
      for t in component.content:
        deps.update(_get_xsd_dependencies(t))
    case xmlschema.XsdType():
      deps.add(component)
    case _:
      pass
  return deps


def _get_xsd_type_dependencies(t: xmlschema.XsdType) -> set[_BaseXsdType]:
  deps = set()

  if t.base_type is not None:
    deps.update(_get_xsd_dependencies(t.base_type))

  match t:
    case xmlschema.validators.XsdUnion():
      for member in t.member_types:
        deps.update(_get_xsd_dependencies(member))
    case xmlschema.validators.simple_types.XsdList():
      deps.update(_get_xsd_dependencies(t.item_type))
    case _XsdComplexType():
      deps.update(_get_xsd_dependencies(t.attributes))
      deps.update(_get_xsd_dependencies(t.content))
    case _:
      pass

  return set(filter(_include_type, deps))


def _gather_xsd_types(
  schema: xmlschema.XMLSchema,
) -> dict[_BaseXsdType, set[_BaseXsdType]]:
  all_types: set[xmlschema.XsdType] = set(schema.types.values())
  all_types.update(e.type for e in schema.elements.values())
  all_types = {x for x in all_types if _include_type(x)}

  type_defs = {}
  while all_types:
    t = all_types.pop()
    deps = _get_xsd_type_dependencies(t)
    type_defs[t] = deps
    all_types.update(dep for dep in deps if dep not in type_defs)
  return type_defs


class _TypeRewriter:
  def __init__(self, type_defs: Sequence[TypeDefinition]) -> None:
    self._type_to_fields = collections.defaultdict(list)
    for type_def in type_defs:
      for field in type_def.get_fields():
        self._type_to_fields[field.proto_type].append(field)

  def rewrite(self, old_type: TypeDefinition, new_type: TypeDefinition) -> None:
    for field in self._type_to_fields[old_type]:
      field.proto_type = new_type

    for t in self._type_to_fields:
      if (
        isinstance(t, TypeDefinition)
        and t.enclosing_type
        and t.enclosing_type[0] is old_type
      ):
        t.enclosing_type = (new_type, t.enclosing_type[1])

    self._type_to_fields[new_type] = self._type_to_fields[old_type]
    del self._type_to_fields[old_type]


def _find_map_fields(
  map_type: TypeDefinition,
  map_override: MapOverrideConfig,
) -> tuple[Field, Field]:
  key_field = val_field = None
  for f in map_type.get_fields():
    if f.name == map_override.key_field:
      key_field = f
    if f.name == map_override.value_field:
      val_field = f

  if key_field is None:
    raise RuntimeError(f"could not find {map_override.key_field} in {map_type}")
  if val_field is None:
    raise RuntimeError(f"could not find {map_override.value_field} in {map_type}")

  return key_field, val_field


def process_xsd(
  xsd_file: xmlschema.aliases.SchemaSourceType
  | list[xmlschema.aliases.SchemaSourceType],
  config: Config | None = None,
) -> tuple[TypeDefinition, ...]:
  schema = xmlschema.XMLSchema(xsd_file)
  if config is None:
    config = Config()

  xsd_type_graph = _gather_xsd_types(schema)
  type_defs: dict[xmlschema.XsdType, TypeDefinition] = {}
  definition_order = tuple(graphlib.TopologicalSorter(xsd_type_graph).static_order())
  for t in definition_order:
    if type_def := _make_definition_for(t, type_defs):
      type_defs[t] = type_def

  rewriter = _TypeRewriter(list(type_defs.values()))

  path_to_type = {}
  for type_def in type_defs.values():
    path_to_type[type_def.path] = type_def
  inv_type_defs = {v: k for k, v in type_defs.items()}

  for map_override in config.map_overrides:
    if map_type := path_to_type.get(map_override.map_type):
      t = inv_type_defs[map_type]
      key_field, val_field = _find_map_fields(map_type, map_override)

      if not isinstance(key_field.proto_type, AtomicType):
        raise RuntimeError(f"expected map key: {key_field} to have an atomic type")
      if not isinstance(val_field.proto_type, AtomicType):
        raise RuntimeError(f"expected map value: {val_field} to have an atomic type")

      new_type_def = MapType(
        comment=map_type.comment,
        name=map_type.name,
        enclosing_type=map_type.enclosing_type,
        key_type=key_field.proto_type,
        value_type=val_field.proto_type,
        key_source=key_field.get_source(),
        value_source=val_field.get_source(),
      )
      rewriter.rewrite(map_type, new_type_def)
      type_defs[t] = new_type_def
      inv_type_defs[new_type_def] = t
      del inv_type_defs[map_type]

  return tuple(type_defs[t] for t in definition_order if t in type_defs)
