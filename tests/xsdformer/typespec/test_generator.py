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


def _generate(
    xsd_str: str,
    namespace: str = "demo",
    config: xsd.Config | None = None,
    *,
    proto_compat: bool = False,
) -> str:
    type_defs = xsd.process_xsd(io.StringIO(xsd_str), config)
    return "\n".join(generator.generate(namespace, type_defs, proto_compat=proto_compat))


def test_scalar_model_golden() -> None:
    assert _generate(_SCALAR_XSD) == (
        "namespace Demo;\n"
        "\n"
        "model Record {\n"
        "  id: string;\n"
        "  ref?: string;\n"
        "  title: string;\n"
        "  comment?: string;\n"
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
        "  author?: string;\n"
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
        "namespace Demo;\n\nmodel Contact {\n  label: string;\n  email?: string;\n  phone?: string;\n}"
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
    map_overrides=(xsd.MapOverrideConfig(map_type=("Entry",), key_field="key", value_field="value"),),
)


def test_map_field_becomes_record_golden() -> None:
    # The `Entry` map type itself emits nothing; the field surfaces as a
    # string-keyed `Record<V>` with no `[]`/`?` (required-but-possibly-empty).
    assert _generate(_MAP_XSD, config=_MAP_CONFIG) == (
        "namespace Demo;\n\nmodel Catalog {\n  entry: Record<string>;\n}"
    )


def test_proto_compat_golden() -> None:
    # proto-compat adds the @typespec/protobuf imports/`using`, `@package`,
    # `@field(N)`, and integer-valued enums (zero member first). Member names and
    # numbers come from the IR, matching what `xsd->proto` emits.
    assert _generate(_ENUM_XSD, proto_compat=True) == (
        'import "@typespec/protobuf";\n'
        "using Protobuf;\n"
        "\n"
        '@package({name: "demo"})\n'
        "namespace Demo;\n"
        "\n"
        "enum Role {\n"
        "  ROLE_UNSPECIFIED: 0,\n"
        "  ROLE_AUTHOR: 1,\n"
        "  ROLE_EDITOR: 2,\n"
        "  ROLE_REVIEWER: 3,\n"
        "}\n"
        "\n"
        "model Person {\n"
        "  @field(1) role: Role;\n"
        "}"
    )


def test_proto_compat_optional_field_golden() -> None:
    # Optional fields keep the `name?: T` marker under proto-compat, after the
    # `@field(N)` decorator. `xs:date` maps to `WellKnown.Timestamp` (not the
    # native `utcDateTime`, which `@typespec/protobuf` cannot lower to a proto
    # scalar) so it matches the protobuf generator's `google.protobuf.Timestamp`.
    assert _generate(_SCALAR_XSD, proto_compat=True) == (
        'import "@typespec/protobuf";\n'
        "using Protobuf;\n"
        "\n"
        '@package({name: "demo"})\n'
        "namespace Demo;\n"
        "\n"
        "model Record {\n"
        "  @field(1) id: string;\n"
        "  @field(2) ref?: string;\n"
        "  @field(3) title: string;\n"
        "  @field(4) comment?: string;\n"
        "  @field(5) tag: string[];\n"
        "  @field(6) count: int32;\n"
        "  @field(7) ratio: float64;\n"
        "  @field(8) active: boolean;\n"
        "  @field(9) created: WellKnown.Timestamp;\n"
        "}"
    )


# An inline enum nested in a complexType: hoisted to `Sample_Origin`, exercising
# the proto-compat enum-member re-prefixing.
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


def test_nested_enum_default_keeps_local_member_names() -> None:
    # Default mode: the hoisted enum keeps the bare proto value name
    # (`ORIGIN_*`) — TypeSpec enum members aren't C++-scoped, so no collision,
    # and the proto<->pydantic converter keys off this name (ADR 0001).
    assert _generate(_NESTED_ENUM_XSD) == (
        "namespace Demo;\n"
        "\n"
        "enum Sample_Origin {\n"
        '  ORIGIN_UNSPECIFIED: "",\n'
        '  ORIGIN_GERMLINE: "germline",\n'
        '  ORIGIN_SOMATIC: "somatic",\n'
        "}\n"
        "\n"
        "model Sample {\n"
        "  origin: Sample_Origin;\n"
        "}"
    )


def test_proto_compat_nested_enum_reprefixes_members() -> None:
    # proto-compat: members are re-prefixed with the full hoisted path
    # (`SAMPLE_ORIGIN_*`) so they stay unique at proto's package scope, where the
    # protobuf generator would instead nest the enum inside `Sample`.
    assert _generate(_NESTED_ENUM_XSD, proto_compat=True) == (
        'import "@typespec/protobuf";\n'
        "using Protobuf;\n"
        "\n"
        '@package({name: "demo"})\n'
        "namespace Demo;\n"
        "\n"
        "enum Sample_Origin {\n"
        "  SAMPLE_ORIGIN_UNSPECIFIED: 0,\n"
        "  SAMPLE_ORIGIN_GERMLINE: 1,\n"
        "  SAMPLE_ORIGIN_SOMATIC: 2,\n"
        "}\n"
        "\n"
        "model Sample {\n"
        "  @field(1) origin: Sample_Origin;\n"
        "}"
    )
