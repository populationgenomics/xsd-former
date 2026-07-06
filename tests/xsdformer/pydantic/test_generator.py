"""Backbone (pure-Python) tests for the pydantic generator — ADR 0002 slice 1.

Golden/structural assertions on the emitted models module: clean-dialect
pydantic v2 (`str, Enum` enums, scalar fields with cardinality, hoisted nested
types as `Parent_Child`, `Choice`-flattened optionals, `dict[str, V]` maps), plus
a `compile()` syntax check. No pydantic runtime or Node toolchain required — the
semantic-equivalence gate against the tsp oracle is slice 5.
"""

import io

import pytest

from xsdformer.pydantic import generator
from xsdformer.xsd import xsd


def _generate(
    xsd_str: str,
    namespace: str = 'demo',
    config: xsd.Config | None = None,
) -> str:
    type_defs = xsd.process_xsd(io.StringIO(xsd_str), config)
    return '\n'.join(generator.generate(namespace, type_defs))


# A flat schema: one complexType of scalar fields exercising each cardinality.
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


def test_scalar_model_golden() -> None:
    # `(1,1)` -> `T`, `(0,1)` -> `T | None = None`, repeated -> `list[T] = []`;
    # `xs:date` -> `datetime`. Only the datetime/BaseModel imports are pulled in.
    assert _generate(_SCALAR_XSD) == (
        'from __future__ import annotations\n'
        '\n'
        'from datetime import datetime\n'
        '\n'
        'from pydantic import BaseModel\n'
        '\n'
        '\n'
        'class Record(BaseModel):\n'
        '    id: str\n'
        '    ref: str | None = None\n'
        '    title: str\n'
        '    comment: str | None = None\n'
        '    tag: list[str] = []\n'
        '    count: int\n'
        '    ratio: float\n'
        '    active: bool\n'
        '    created: datetime'
    )


# A named simpleType enumeration plus a message that references it.
_ENUM_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:simpleType name="role">
    <xs:restriction base="xs:string">
      <xs:enumeration value="author" />
      <xs:enumeration value="editor" />
      <xs:enumeration value="reviewer" />
    </xs:restriction>
  </xs:simpleType>
  <xs:complexType name="person">
    <xs:sequence>
      <xs:element name="role" type="role" />
    </xs:sequence>
  </xs:complexType>
  <xs:element name="person" type="person" />
</xs:schema>
"""


def test_enum_string_valued_golden() -> None:
    # `str, Enum`; member name = proto value name, value = xml_value; synthesized
    # "" zero member first. The enum (a dependency) is emitted before the model.
    assert _generate(_ENUM_XSD) == (
        'from __future__ import annotations\n'
        '\n'
        'from enum import Enum\n'
        '\n'
        'from pydantic import BaseModel\n'
        '\n'
        '\n'
        'class Role(str, Enum):\n'
        '    ROLE_UNSPECIFIED = ""\n'
        '    ROLE_AUTHOR = "author"\n'
        '    ROLE_EDITOR = "editor"\n'
        '    ROLE_REVIEWER = "reviewer"\n'
        '\n'
        '\n'
        'class Person(BaseModel):\n'
        '    role: Role'
    )


# Documentation on a complexType, an element, and a simpleType enumeration.
_DOC_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:simpleType name="role">
    <xs:annotation><xs:documentation>The contributor's role.</xs:documentation></xs:annotation>
    <xs:restriction base="xs:string">
      <xs:enumeration value="author" />
    </xs:restriction>
  </xs:simpleType>
  <xs:complexType name="person">
    <xs:annotation><xs:documentation>A contributor record.</xs:documentation></xs:annotation>
    <xs:sequence>
      <xs:element name="name" type="xs:string">
        <xs:annotation><xs:documentation>The display name.</xs:documentation></xs:annotation>
      </xs:element>
      <xs:element name="role" type="role" />
    </xs:sequence>
  </xs:complexType>
  <xs:element name="person" type="person" />
</xs:schema>
"""


def test_doc_comments_as_class_docstrings_golden() -> None:
    # Type docs become class docstrings; the field-level doc on `name` is dropped
    # (clean dialect — only structure survives; the gate normalizes descriptions).
    assert _generate(_DOC_XSD) == (
        'from __future__ import annotations\n'
        '\n'
        'from enum import Enum\n'
        '\n'
        'from pydantic import BaseModel\n'
        '\n'
        '\n'
        'class Role(str, Enum):\n'
        '    """The contributor\'s role."""\n'
        '\n'
        '    ROLE_UNSPECIFIED = ""\n'
        '    ROLE_AUTHOR = "author"\n'
        '\n'
        '\n'
        'class Person(BaseModel):\n'
        '    """A contributor record."""\n'
        '\n'
        '    name: str\n'
        '    role: Role'
    )


# An element with an inline (anonymous) complexType: a nested type that must be
# hoisted to module scope as `Parent_Child`.
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


def test_nested_type_hoisted_golden() -> None:
    # The anonymous `book` type is hoisted to `Library_Book` and referenced by
    # that name. As a dependency it is emitted first.
    assert _generate(_NESTED_XSD) == (
        'from __future__ import annotations\n'
        '\n'
        'from pydantic import BaseModel\n'
        '\n'
        '\n'
        'class Library_Book(BaseModel):\n'
        '    title: str\n'
        '    author: str | None = None\n'
        '\n'
        '\n'
        'class Library(BaseModel):\n'
        '    book: Library_Book'
    )


# A choice nested within a sequence: its members flatten to optional fields,
# while the sibling sequence field keeps its own cardinality.
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


def test_choice_flattened_to_optional_golden() -> None:
    # `label` stays required; the choice branches `email`/`phone` become optional
    # even though each is individually required.
    assert _generate(_CHOICE_XSD) == (
        'from __future__ import annotations\n'
        '\n'
        'from pydantic import BaseModel\n'
        '\n'
        '\n'
        'class Contact(BaseModel):\n'
        '    label: str\n'
        '    email: str | None = None\n'
        '    phone: str | None = None'
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


def test_map_field_becomes_dict_golden() -> None:
    # The `Entry` map type emits nothing; the field surfaces as `dict[str, V] = {}`
    # (required-but-possibly-empty — no `| None`).
    assert _generate(_MAP_XSD, config=_MAP_CONFIG) == (
        'from __future__ import annotations\n'
        '\n'
        'from pydantic import BaseModel\n'
        '\n'
        '\n'
        'class Catalog(BaseModel):\n'
        '    entry: dict[str, str] = {}'
    )


# Field names that collide with Python keywords, plus a safe one.
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


def test_keyword_field_names_aliased_golden() -> None:
    # `class`/`import` are Python keywords, so they are suffixed with `_` and given
    # a `Field(alias=...)`; the model gains `populate_by_name`. `value` stays bare.
    assert _generate(_KEYWORD_XSD) == (
        'from __future__ import annotations\n'
        '\n'
        'from pydantic import BaseModel, ConfigDict, Field\n'
        '\n'
        '\n'
        'class Thing(BaseModel):\n'
        '    model_config = ConfigDict(populate_by_name=True)\n'
        '\n'
        '    class_: str = Field(alias="class")\n'
        '    import_: str | None = Field(default=None, alias="import")\n'
        '    value: str'
    )


# An inline enum nested in a complexType: hoisted to `Sample_Origin`.
_NESTED_ENUM_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:complexType name="sample">
    <xs:sequence>
      <xs:element name="origin">
        <xs:simpleType>
          <xs:restriction base="xs:string">
            <xs:enumeration value="germline" />
            <xs:enumeration value="somatic" />
          </xs:restriction>
        </xs:simpleType>
      </xs:element>
    </xs:sequence>
  </xs:complexType>
  <xs:element name="sample" type="sample" />
</xs:schema>
"""


def test_nested_enum_hoisted_golden() -> None:
    # The inline enum is hoisted to `Sample_Origin`, keeping the bare proto value
    # name (`ORIGIN_*`) as the member name — the converter's identity key.
    assert _generate(_NESTED_ENUM_XSD) == (
        'from __future__ import annotations\n'
        '\n'
        'from enum import Enum\n'
        '\n'
        'from pydantic import BaseModel\n'
        '\n'
        '\n'
        'class Sample_Origin(str, Enum):\n'
        '    ORIGIN_UNSPECIFIED = ""\n'
        '    ORIGIN_GERMLINE = "germline"\n'
        '    ORIGIN_SOMATIC = "somatic"\n'
        '\n'
        '\n'
        'class Sample(BaseModel):\n'
        '    origin: Sample_Origin'
    )


# An enum whose value carries characters that must be escaped in a Python string
# literal (a double quote and a backslash).
_ESCAPE_ENUM_XSD = r"""
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:simpleType name="quote">
    <xs:restriction base="xs:string">
      <xs:enumeration value='say "hi"' />
      <xs:enumeration value="back\slash" />
    </xs:restriction>
  </xs:simpleType>
  <xs:element name="quote" type="quote" />
</xs:schema>
"""


def test_enum_string_values_escaped_golden() -> None:
    # Double quotes and backslashes in the xml_value are escaped so the emitted
    # Python string literal stays valid. No model/pydantic import (enum only).
    assert _generate(_ESCAPE_ENUM_XSD) == (
        'from __future__ import annotations\n'
        '\n'
        'from enum import Enum\n'
        '\n'
        '\n'
        'class Quote(str, Enum):\n'
        '    QUOTE_UNSPECIFIED = ""\n'
        '    QUOTE_SAY_HI = "say \\"hi\\""\n'
        '    QUOTE_BACK_SLASH = "back\\\\slash"'
    )


def _leaf(name: str, proto_type: xsd.AtomicType) -> xsd.Elem:
    elem = xsd.Elem(
        documentation=None,
        name=name,
        occurs=(1, 1),
        proto_type=proto_type,
        default=None,
        source=xsd.XMLElemSource(elem=name),
    )
    elem.computed_occurs = (1, 1)
    return elem


def test_conflicting_choice_branches_raise() -> None:
    # Two Choice branches sharing a name but resolving to different types can't be
    # expressed in valid XSD (the EDC constraint), but a rename transform could
    # synthesize it. The "first wins" dedup would silently drop the second's type;
    # the generators raise instead of emitting a model that loses schema shape.
    message = xsd.Message(
        documentation=None,
        name='Rec',
        content=(
            xsd.Choice(
                documentation=None,
                occurs=(1, 1),
                content=(
                    _leaf('x', xsd.AtomicType.STRING),
                    _leaf('x', xsd.AtomicType.INT32),
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match='conflicting type'):
        '\n'.join(generator.generate('demo', (message,)))


def test_duplicate_same_shape_field_is_deduped() -> None:
    # Same name *and* same shape is harmless — dedup silently, no raise.
    same = (_leaf('x', xsd.AtomicType.STRING), _leaf('x', xsd.AtomicType.STRING))
    message = xsd.Message(
        documentation=None,
        name='Rec',
        content=(xsd.Choice(documentation=None, occurs=(1, 1), content=same),),
    )
    code = '\n'.join(generator.generate('demo', (message,)))
    assert code.count('x:') == 1


def test_all_fixtures_compile() -> None:
    # Every emitted module must be syntactically valid Python. (Semantic /
    # importability checks come once pydantic is a dependency — slice 4/5.)
    for xsd_str, config in (
        (_SCALAR_XSD, None),
        (_ENUM_XSD, None),
        (_DOC_XSD, None),
        (_NESTED_XSD, None),
        (_CHOICE_XSD, None),
        (_MAP_XSD, _MAP_CONFIG),
        (_KEYWORD_XSD, None),
        (_NESTED_ENUM_XSD, None),
        (_ESCAPE_ENUM_XSD, None),
    ):
        code = _generate(xsd_str, config=config)
        compile(code, '<generated>', 'exec')
