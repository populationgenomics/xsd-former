"""Backbone (pure-Python) tests for the proto <-> pydantic converter (ADR 0002 slice 3).

Golden/structural assertions on the emitted `pydantic_converter.py`: the
`*_from_proto` / `*_to_proto` pairs that bridge compiled protobuf classes and the
pydantic models, plus a `compile()` syntax check. No pydantic or protobuf runtime
required — the round-trip equivalence over real records is slice 6.

The converter is driven by the same IR signals as the pydantic generator
(`proto_type`, cardinality, Choice flattening, keyword aliasing) and the protobuf
generator (`oneof` formation, proto3 `optional` presence), so these fixtures
mirror `test_generator.py`'s to keep the two emitters in lockstep.
"""

import io

from xsdformer.pydantic import converter
from xsdformer.xsd import xsd


def _generate(xsd_str: str, config: xsd.Config | None = None) -> str:
    type_defs = xsd.process_xsd(io.StringIO(xsd_str), config)
    return '\n'.join(converter.generate('demo', type_defs, 'demo_pb2'))


# Scalars across every cardinality, plus a date and required/optional attributes.
_SCALAR_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:complexType name="record">
    <xs:sequence>
      <xs:element name="title" type="xs:string" />
      <xs:element name="comment" type="xs:string" minOccurs="0" />
      <xs:element name="tag" type="xs:string" maxOccurs="unbounded" />
      <xs:element name="count" type="xs:int" />
      <xs:element name="ratio" type="xs:double" />
      <xs:element name="active" type="xs:boolean" />
      <xs:element name="created" type="xs:date" />
    </xs:sequence>
    <xs:attribute name="id" type="xs:ID" use="required" />
    <xs:attribute name="ref" type="xs:string" use="optional" />
  </xs:complexType>
  <xs:element name="record" type="record" />
</xs:schema>
"""


def test_scalar_converter_golden() -> None:
    # Optional scalars read through `HasField` and write under an `is not None`
    # guard (ADR 0002 R1 presence); repeated -> `list`/`extend`; `xs:date` <->
    # `Timestamp` via `ToDatetime`/`FromDatetime`; required scalars are direct.
    assert _generate(_SCALAR_XSD) == (
        'import demo_pb2\n'
        'import models\n'
        '\n'
        '\n'
        'def Record_from_proto(proto):\n'
        '    return models.Record(\n'
        '        id=proto.id,\n'
        "        ref=proto.ref if proto.HasField('ref') else None,\n"
        '        title=proto.title,\n'
        "        comment=proto.comment if proto.HasField('comment') else None,\n"
        '        tag=list(proto.tag),\n'
        '        count=proto.count,\n'
        '        ratio=proto.ratio,\n'
        '        active=proto.active,\n'
        '        created=proto.created.ToDatetime(),\n'
        '    )\n'
        '\n'
        '\n'
        'def Record_to_proto(model):\n'
        '    proto = demo_pb2.Record()\n'
        '    proto.id = model.id\n'
        '    if model.ref is not None:\n'
        '        proto.ref = model.ref\n'
        '    proto.title = model.title\n'
        '    if model.comment is not None:\n'
        '        proto.comment = model.comment\n'
        '    proto.tag.extend(model.tag)\n'
        '    proto.count = model.count\n'
        '    proto.ratio = model.ratio\n'
        '    proto.active = model.active\n'
        '    proto.created.FromDatetime(model.created)\n'
        '    return proto'
    )


# A named enum used both singularly and repeated.
_ENUM_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:simpleType name="role">
    <xs:restriction base="xs:string">
      <xs:enumeration value="author" />
      <xs:enumeration value="editor" />
    </xs:restriction>
  </xs:simpleType>
  <xs:complexType name="person">
    <xs:sequence>
      <xs:element name="role" type="role" />
      <xs:element name="tags" type="role" maxOccurs="unbounded" />
    </xs:sequence>
  </xs:complexType>
  <xs:element name="person" type="person" />
</xs:schema>
"""


def test_enum_converter_golden() -> None:
    # Enums are keyed by member name (= proto value name): `proto -> pydantic`
    # indexes `models.Role[demo_pb2.Role.Name(v)]`, `pydantic -> proto` writes
    # `demo_pb2.Role.Value(v.name)`. The enum type itself emits no functions.
    assert _generate(_ENUM_XSD) == (
        'import demo_pb2\n'
        'import models\n'
        '\n'
        '\n'
        'def Person_from_proto(proto):\n'
        '    return models.Person(\n'
        '        role=models.Role[demo_pb2.Role.Name(proto.role)],\n'
        '        tags=[models.Role[demo_pb2.Role.Name(v)] for v in proto.tags],\n'
        '    )\n'
        '\n'
        '\n'
        'def Person_to_proto(model):\n'
        '    proto = demo_pb2.Person()\n'
        '    proto.role = demo_pb2.Role.Value(model.role.name)\n'
        '    proto.tags.extend(demo_pb2.Role.Value(v.name) for v in model.tags)\n'
        '    return proto'
    )


# An element with an inline complexType: a nested type hoisted to `Parent_Child`.
_NESTED_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:complexType name="library">
    <xs:sequence>
      <xs:element name="book">
        <xs:complexType>
          <xs:sequence>
            <xs:element name="title" type="xs:string" />
            <xs:element name="author" type="xs:string" minOccurs="0" />
          </xs:sequence>
        </xs:complexType>
      </xs:element>
    </xs:sequence>
  </xs:complexType>
  <xs:element name="library" type="library" />
</xs:schema>
"""


def test_nested_message_converter_golden() -> None:
    # The hoisted `Library_Book` model bridges proto `Library.Book`; a singular
    # message field round-trips via the child pair + `CopyFrom`.
    assert _generate(_NESTED_XSD) == (
        'import demo_pb2\n'
        'import models\n'
        '\n'
        '\n'
        'def Library_Book_from_proto(proto):\n'
        '    return models.Library_Book(\n'
        '        title=proto.title,\n'
        "        author=proto.author if proto.HasField('author') else None,\n"
        '    )\n'
        '\n'
        '\n'
        'def Library_Book_to_proto(model):\n'
        '    proto = demo_pb2.Library.Book()\n'
        '    proto.title = model.title\n'
        '    if model.author is not None:\n'
        '        proto.author = model.author\n'
        '    return proto\n'
        '\n'
        '\n'
        'def Library_from_proto(proto):\n'
        '    return models.Library(\n'
        '        book=Library_Book_from_proto(proto.book),\n'
        '    )\n'
        '\n'
        '\n'
        'def Library_to_proto(model):\n'
        '    proto = demo_pb2.Library()\n'
        '    proto.book.CopyFrom(Library_Book_to_proto(model.book))\n'
        '    return proto'
    )


# A choice that becomes a proto `oneof`: flat optionals in the model, with a
# multi-branch guard on the way back to proto.
_CHOICE_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:complexType name="contact">
    <xs:sequence>
      <xs:element name="label" type="xs:string" />
      <xs:choice>
        <xs:element name="email" type="xs:string" />
        <xs:element name="phone" type="xs:string" />
      </xs:choice>
    </xs:sequence>
  </xs:complexType>
  <xs:element name="contact" type="contact" />
</xs:schema>
"""


def test_choice_oneof_guard_golden() -> None:
    # `proto -> pydantic` needs no check (proto guarantees <=1); `pydantic ->
    # proto` raises when more than one branch of the proto `oneof` is set, since
    # proto cannot represent it (ADR 0002 "Choice enforcement").
    assert _generate(_CHOICE_XSD) == (
        'import demo_pb2\n'
        'import models\n'
        '\n'
        '\n'
        'def Contact_from_proto(proto):\n'
        '    return models.Contact(\n'
        '        label=proto.label,\n'
        "        email=proto.email if proto.HasField('email') else None,\n"
        "        phone=proto.phone if proto.HasField('phone') else None,\n"
        '    )\n'
        '\n'
        '\n'
        'def Contact_to_proto(model):\n'
        '    proto = demo_pb2.Contact()\n'
        '    if sum(x is not None for x in (model.email, model.phone)) > 1:\n'
        '        raise ValueError("at most one of email, phone may be set in Contact")\n'
        '    proto.label = model.label\n'
        '    if model.email is not None:\n'
        '        proto.email = model.email\n'
        '    if model.phone is not None:\n'
        '        proto.phone = model.phone\n'
        '    return proto'
    )


# A repeated key/value element promoted to a map via a map-override config.
_MAP_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:complexType name="entry">
    <xs:attribute name="key" type="xs:string" use="required" />
    <xs:attribute name="value" type="xs:string" use="required" />
  </xs:complexType>
  <xs:complexType name="catalog">
    <xs:sequence>
      <xs:element name="entry" type="entry" maxOccurs="unbounded" />
    </xs:sequence>
  </xs:complexType>
  <xs:element name="catalog" type="catalog" />
</xs:schema>
"""

_MAP_CONFIG = xsd.Config(
    map_overrides=(xsd.MapOverrideConfig(map_type=('Entry',), key_field='key', value_field='value'),),
)


def test_map_converter_golden() -> None:
    # A scalar-valued map round-trips via `dict(proto.f)` / `proto.f.update(...)`;
    # the `Entry` map type itself emits no functions.
    assert _generate(_MAP_XSD, _MAP_CONFIG) == (
        'import demo_pb2\n'
        'import models\n'
        '\n'
        '\n'
        'def Catalog_from_proto(proto):\n'
        '    return models.Catalog(\n'
        '        entry=dict(proto.entry),\n'
        '    )\n'
        '\n'
        '\n'
        'def Catalog_to_proto(model):\n'
        '    proto = demo_pb2.Catalog()\n'
        '    proto.entry.update(model.entry)\n'
        '    return proto'
    )


# Field names colliding with Python keywords: the proto side keeps the bare name
# (accessed via getattr/setattr), the model side carries the `_`-suffixed alias.
_KEYWORD_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:complexType name="thing">
    <xs:sequence>
      <xs:element name="class" type="xs:string" />
      <xs:element name="import" type="xs:string" minOccurs="0" />
      <xs:element name="value" type="xs:string" />
    </xs:sequence>
  </xs:complexType>
  <xs:element name="thing" type="thing" />
</xs:schema>
"""


def test_keyword_field_converter_golden() -> None:
    # `class`/`import` are valid proto field names but not Python attributes, so
    # the converter reaches them with `getattr`/`setattr`; the model attribute is
    # the keyword generator's `_`-suffixed alias (`class_`, `import_`).
    assert _generate(_KEYWORD_XSD) == (
        'import demo_pb2\n'
        'import models\n'
        '\n'
        '\n'
        'def Thing_from_proto(proto):\n'
        '    return models.Thing(\n'
        "        class_=getattr(proto, 'class'),\n"
        "        import_=getattr(proto, 'import') if proto.HasField('import') else None,\n"
        '        value=proto.value,\n'
        '    )\n'
        '\n'
        '\n'
        'def Thing_to_proto(model):\n'
        '    proto = demo_pb2.Thing()\n'
        "    setattr(proto, 'class', model.class_)\n"
        '    if model.import_ is not None:\n'
        "        setattr(proto, 'import', model.import_)\n"
        '    proto.value = model.value\n'
        '    return proto'
    )


def test_all_fixtures_compile() -> None:
    # Every emitted converter module must be syntactically valid Python.
    for xsd_str, config in (
        (_SCALAR_XSD, None),
        (_ENUM_XSD, None),
        (_NESTED_XSD, None),
        (_CHOICE_XSD, None),
        (_MAP_XSD, _MAP_CONFIG),
        (_KEYWORD_XSD, None),
    ):
        code = _generate(xsd_str, config)
        compile(code, '<generated>', 'exec')
