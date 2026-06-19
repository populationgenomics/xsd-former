"""Backbone (pure-Python) tests for the TypeSpec generator — slices 2-4 (ADR 0001).

Golden/structural assertions on the emitted `.tsp`: a namespace of flat `model`s
with scalar fields, cardinality, string-valued enums, doc-comments, hoisted
nested types (`Parent_Child`), and `Choice`-flattened optional properties. No
Node toolchain required.
"""

import io

from xsdformer.typespec import generator
from xsdformer.xsd import xsd

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


def _generate(xsd_str: str, namespace: str = "demo") -> str:
    type_defs = xsd.process_xsd(io.StringIO(xsd_str))
    return "\n".join(generator.generate(namespace, type_defs))


def test_scalar_model_golden() -> None:
    assert _generate(_SCALAR_XSD) == (
        "namespace Demo;\n"
        "\n"
        "model Record {\n"
        "  id: string;\n"
        "  ref: string?;\n"
        "  title: string;\n"
        "  comment: string?;\n"
        "  tag: string[];\n"
        "  count: int32;\n"
        "  ratio: float64;\n"
        "  active: boolean;\n"
        "  created: utcDateTime;\n"
        "}"
    )


def test_namespace_pascal_cased() -> None:
    out = _generate(_SCALAR_XSD, namespace="my_package")
    assert out.startswith("namespace MyPackage;\n")


def test_dotted_namespace_pascal_cased_per_component() -> None:
    out = _generate(_SCALAR_XSD, namespace="org.my_package.v1")
    assert out.startswith("namespace Org.MyPackage.V1;\n")


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
    # Member name = proto value name; value = xml_value; synthesized "" zero first.
    assert _generate(_ENUM_XSD) == (
        "namespace Demo;\n"
        "\n"
        "enum Role {\n"
        '  ROLE_UNSPECIFIED: "",\n'
        '  ROLE_AUTHOR: "author",\n'
        '  ROLE_EDITOR: "editor",\n'
        '  ROLE_REVIEWER: "reviewer",\n'
        "}\n"
        "\n"
        "model Person {\n"
        "  role: Role;\n"
        "}"
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


def test_doc_comments_golden() -> None:
    assert _generate(_DOC_XSD) == (
        "namespace Demo;\n"
        "\n"
        "/** The contributor's role. */\n"
        "enum Role {\n"
        '  ROLE_UNSPECIFIED: "",\n'
        '  ROLE_AUTHOR: "author",\n'
        "}\n"
        "\n"
        "/** A contributor record. */\n"
        "model Person {\n"
        "  /** The display name. */\n"
        "  name: string;\n"
        "  role: Role;\n"
        "}"
    )


# An element with an inline (anonymous) complexType: a nested type that must be
# hoisted to the top-level namespace as `Parent_Child`.
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
    # The anonymous `book` type is hoisted to `Library_Book` and the parent
    # references it by that name. The nested type, being a dependency, is
    # emitted first.
    assert _generate(_NESTED_XSD) == (
        "namespace Demo;\n"
        "\n"
        "model Library_Book {\n"
        "  title: string;\n"
        "  author: string?;\n"
        "}\n"
        "\n"
        "model Library {\n"
        "  book: Library_Book;\n"
        "}"
    )


# A choice nested within a sequence: its members flatten to optional properties,
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
    # `label` (outside the choice) stays required; the choice branches `email`
    # and `phone` become optional even though each is individually required.
    assert _generate(_CHOICE_XSD) == (
        "namespace Demo;\n\nmodel Contact {\n  label: string;\n  email: string?;\n  phone: string?;\n}"
    )
