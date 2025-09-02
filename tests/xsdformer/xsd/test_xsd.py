import io

from xsdformer.xsd import xsd

_TEST_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:complexType name="TestType">
    <xs:sequence>
      <xs:element name="name" type="xs:string" />
      <xs:element name="age" type="xs:int" minOccurs="0" />
    </xs:sequence>
    <xs:attribute name="id" type="xs:ID" use="required" />
    <xs:attribute name="status" type="xs:string" default="active" />
  </xs:complexType>

  <xs:element name="root" type="TestType" />
</xs:schema>
"""


def test_process_xsd() -> None:
  type_defs = xsd.process_xsd(io.StringIO(_TEST_XSD))
  type_defs_by_name = {t.name: t for t in type_defs}

  elem_defs = list(type_defs_by_name["TestType"].get_fields())

  assert len(elem_defs) == 4

  defs_by_name = {e.name: e for e in elem_defs}

  # Test element 'name'
  assert "name" in defs_by_name
  name_def = defs_by_name["name"]
  assert name_def.occurs == (1, 1)
  assert name_def.proto_type is xsd.AtomicType.STRING

  # Test element 'age'
  assert "age" in defs_by_name
  age_def = defs_by_name["age"]
  assert age_def.occurs == (0, 1)
  assert age_def.proto_type == xsd.AtomicType.INT32

  # Test attribute 'id'
  assert "id" in defs_by_name
  id_def = defs_by_name["id"]
  assert id_def.occurs == (1, 1)
  assert id_def.proto_type is xsd.AtomicType.ID

  # Test attribute 'status'
  assert "status" in defs_by_name
  status_def = defs_by_name["status"]
  assert status_def.occurs == (0, 1)
  assert status_def.proto_type is xsd.AtomicType.STRING
  assert isinstance(status_def, xsd.Attr)
  assert status_def.default == "active"

  # Test root element
  assert "TestType" in type_defs_by_name
  assert type_defs_by_name["TestType"].name == "TestType"


def test_multiply_occurs() -> None:
  assert xsd.multiply_occurs((1, 1), (1, 1)) == (1, 1)
  assert xsd.multiply_occurs((0, 1), (1, 1)) == (0, 1)
  assert xsd.multiply_occurs((1, 1), (0, 1)) == (0, 1)
  assert xsd.multiply_occurs((0, 1), (0, 1)) == (0, 1)
  assert xsd.multiply_occurs((1, None), (1, 1)) == (1, None)
  assert xsd.multiply_occurs((1, 1), (1, None)) == (1, None)
  assert xsd.multiply_occurs((0, 1), (1, None)) == (0, None)
  assert xsd.multiply_occurs((1, None), (0, 1)) == (0, None)
  assert xsd.multiply_occurs((2, 3), (4, 5)) == (8, 15)
  assert xsd.multiply_occurs((2, 3), (0, 5)) == (0, 15)
  assert xsd.multiply_occurs((0, 3), (4, 5)) == (0, 15)
  assert xsd.multiply_occurs((2, None), (4, 5)) == (8, None)
  assert xsd.multiply_occurs((2, 3), (4, None)) == (8, None)


def test_remove_common_prefix() -> None:
  assert xsd._remove_common_prefix(("a", "b", "c"), ("a", "b", "d")) == (("c",), ("d",))
  assert xsd._remove_common_prefix(("a", "b", "c"), ("a", "x", "y")) == (
    ("b", "c"),
    ("x", "y"),
  )
  assert xsd._remove_common_prefix(("a", "b", "c"), ("d", "e", "f")) == (
    ("a", "b", "c"),
    ("d", "e", "f"),
  )
  assert xsd._remove_common_prefix((), ("a", "b", "c")) == ((), ("a", "b", "c"))
  assert xsd._remove_common_prefix(("a", "b", "c"), ()) == (("a", "b", "c"), ())
  assert xsd._remove_common_prefix((), ()) == ((), ())
  assert xsd._remove_common_prefix(("a",), ("a",)) == ((), ())
  assert xsd._remove_common_prefix(("a", "b"), ("a",)) == (("b",), ())
  assert xsd._remove_common_prefix(("a",), ("a", "b")) == ((), ("b",))


_ENCLOSING_TYPE_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:complexType name="OuterType">
    <xs:sequence>
      <xs:element name="outer_field" type="xs:string" />
      <xs:element name="inner_element">
        <xs:complexType>
          <xs:sequence>
            <xs:element name="inner_field" type="xs:int" />
          </xs:sequence>
        </xs:complexType>
      </xs:element>
    </xs:sequence>
  </xs:complexType>

  <xs:element name="root" type="OuterType" />
</xs:schema>
"""


def test_enclosing_type() -> None:
  type_defs = xsd.process_xsd(io.StringIO(_ENCLOSING_TYPE_XSD))
  type_defs_by_name = {t.name: t for t in type_defs if t.name}

  assert "OuterType" in type_defs_by_name
  outer_type = type_defs_by_name["OuterType"]
  assert outer_type.enclosing_type is None
  assert outer_type.path == ("OuterType",)

  outer_fields = {f.name: f for f in outer_type.get_fields()}
  assert "inner_element" in outer_fields
  inner_element_field = outer_fields["inner_element"]

  inner_type = inner_element_field.proto_type
  assert isinstance(inner_type, xsd.Message)
  assert inner_type.name == "InnerElement"
  assert inner_type.enclosing_type is not None
  assert inner_type.enclosing_type[0] is outer_type
  assert inner_type.enclosing_type[1] is inner_element_field
  assert inner_type.path == ("OuterType", "InnerElement")

  inner_fields = {f.name: f for f in inner_type.get_fields()}
  assert "inner_field" in inner_fields


_DEEPLY_NESTED_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:complexType name="Level1Type">
    <xs:sequence>
      <xs:element name="level2">
        <xs:complexType>
          <xs:sequence>
            <xs:element name="level3">
                <xs:complexType>
                  <xs:sequence>
                    <xs:element name="some_field" type="xs:string"/>
                  </xs:sequence>
                </xs:complexType>
            </xs:element>
          </xs:sequence>
        </xs:complexType>
      </xs:element>
    </xs:sequence>
  </xs:complexType>
  <xs:element name="root" type="Level1Type" />
</xs:schema>
"""


def test_deeply_nested_enclosing_type() -> None:
  type_defs = xsd.process_xsd(io.StringIO(_DEEPLY_NESTED_XSD))
  type_defs_by_name = {t.name: t for t in type_defs if t.name}

  assert "Level1Type" in type_defs_by_name
  level1_type = type_defs_by_name["Level1Type"]
  assert level1_type.path == ("Level1Type",)

  level1_fields = {f.name: f for f in level1_type.get_fields()}
  level2_field = level1_fields["level_2"]
  level2_type = level2_field.proto_type

  assert isinstance(level2_type, xsd.Message)
  assert level2_type.name == "Level2"
  assert level2_type.enclosing_type is not None
  assert level2_type.enclosing_type[0] is level1_type
  assert level2_type.path == ("Level1Type", "Level2")

  level2_fields = {f.name: f for f in level2_type.get_fields()}
  level3_field = level2_fields["level_3"]
  level3_type = level3_field.proto_type

  assert isinstance(level3_type, xsd.Message)
  assert level3_type.name == "Level3"
  assert level3_type.enclosing_type is not None
  assert level3_type.enclosing_type[0] is level2_type
  assert level3_type.path == ("Level1Type", "Level2", "Level3")

  level3_fields = {f.name: f for f in level3_type.get_fields()}
  assert "some_field" in level3_fields


_NESTED_IN_CHOICE_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:complexType name="ChoiceType">
    <xs:choice>
      <xs:element name="option1">
        <xs:complexType>
          <xs:sequence>
            <xs:element name="field1" type="xs:string"/>
          </xs:sequence>
        </xs:complexType>
      </xs:element>
      <xs:element name="option2">
        <xs:complexType>
          <xs:sequence>
            <xs:element name="field2" type="xs:int"/>
          </xs:sequence>
        </xs:complexType>
      </xs:element>
    </xs:choice>
  </xs:complexType>
  <xs:element name="root" type="ChoiceType" />
</xs:schema>
"""


def test_nested_in_choice_enclosing_type() -> None:
  type_defs = xsd.process_xsd(io.StringIO(_NESTED_IN_CHOICE_XSD))
  type_defs_by_name = {t.name: t for t in type_defs if t.name}

  assert "ChoiceType" in type_defs_by_name
  choice_type = type_defs_by_name["ChoiceType"]
  assert choice_type.path == ("ChoiceType",)

  choice_fields = {f.name: f for f in choice_type.get_fields()}

  option1_field = choice_fields["option_1"]
  option1_type = option1_field.proto_type
  assert isinstance(option1_type, xsd.Message)
  assert option1_type.name == "Option1"
  assert option1_type.enclosing_type is not None
  assert option1_type.enclosing_type[0] is choice_type
  assert option1_type.path == ("ChoiceType", "Option1")

  option2_field = choice_fields["option_2"]
  option2_type = option2_field.proto_type
  assert isinstance(option2_type, xsd.Message)
  assert option2_type.name == "Option2"
  assert option2_type.enclosing_type is not None
  assert option2_type.enclosing_type[0] is choice_type
  assert option2_type.path == ("ChoiceType", "Option2")


_ENUM_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:simpleType name="EnumType">
    <xs:restriction base="xs:string">
      <xs:enumeration value="VALUE1"/>
      <xs:enumeration value="VALUE2"/>
      <xs:enumeration value="VALUE3"/>
    </xs:restriction>
  </xs:simpleType>

  <xs:complexType name="RootType">
    <xs:sequence>
      <xs:element name="enum_field" type="EnumType"/>
    </xs:sequence>
  </xs:complexType>

  <xs:element name="root" type="RootType" />
</xs:schema>
"""


def test_enumeration() -> None:
  type_defs = xsd.process_xsd(io.StringIO(_ENUM_XSD))
  type_defs_by_name = {t.name: t for t in type_defs if t.name}

  assert "EnumType" in type_defs_by_name
  enum_type = type_defs_by_name["EnumType"]
  assert isinstance(enum_type, xsd.Enumeration)
  assert enum_type.name == "EnumType"
  assert enum_type.enum_values == ("VALUE1", "VALUE2", "VALUE3")

  enum_fields = list(enum_type.field_iter())
  assert len(enum_fields) == 4
  assert enum_fields[0].name == "ENUM_TYPE_UNSPECIFIED"
  assert enum_fields[0].num == 0
  assert enum_fields[0].xml_value is None

  assert enum_fields[1].name == "ENUM_TYPE_VALUE1"
  assert enum_fields[1].num == 1
  assert enum_fields[1].xml_value == "VALUE1"

  assert enum_fields[2].name == "ENUM_TYPE_VALUE2"
  assert enum_fields[2].num == 2
  assert enum_fields[2].xml_value == "VALUE2"

  assert enum_fields[3].name == "ENUM_TYPE_VALUE3"
  assert enum_fields[3].num == 3
  assert enum_fields[3].xml_value == "VALUE3"


_COMPLEX_TYPE_EXTENSION_WITH_ENUM_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:complexType name="BaseType">
    <xs:sequence>
      <xs:element name="enum_field">
        <xs:simpleType>
          <xs:restriction base="xs:string">
            <xs:enumeration value="A"/>
            <xs:enumeration value="B"/>
          </xs:restriction>
        </xs:simpleType>
      </xs:element>
    </xs:sequence>
  </xs:complexType>

  <xs:complexType name="ExtendedType">
    <xs:complexContent>
      <xs:extension base="BaseType">
        <xs:sequence>
          <xs:element name="extended_field" type="xs:int"/>
        </xs:sequence>
      </xs:extension>
    </xs:complexContent>
  </xs:complexType>

  <xs:element name="root" type="ExtendedType" />
</xs:schema>
"""


def test_complex_type_extension_with_enum() -> None:
  type_defs = xsd.process_xsd(io.StringIO(_COMPLEX_TYPE_EXTENSION_WITH_ENUM_XSD))
  type_defs_by_name = {t.name: t for t in type_defs if t.name}

  assert "ExtendedType" in type_defs_by_name
  extended_type = type_defs_by_name["ExtendedType"]
  assert isinstance(extended_type, xsd.Message)

  extended_fields = {f.name: f for f in extended_type.get_fields()}
  assert "enum_field" in extended_fields
  assert "extended_field" in extended_fields

  enum_field = extended_fields["enum_field"]
  enum_type = enum_field.proto_type
  assert isinstance(enum_type, xsd.Enumeration)
  assert enum_type.name == "EnumField"
  assert enum_type.enum_values == ("A", "B")


_MIXED_COMPLEX_TYPE_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:complexType name="MixedType" mixed="true">
    <xs:attribute name="id" />
  </xs:complexType>
  <xs:element name="root" type="MixedType" />
</xs:schema>
"""


def test_mixed_complex_type() -> None:
  type_defs = xsd.process_xsd(io.StringIO(_MIXED_COMPLEX_TYPE_XSD))
  type_defs_by_name = {t.name: t for t in type_defs if t.name}

  assert "MixedType" in type_defs_by_name
  mixed_type = type_defs_by_name["MixedType"]
  assert isinstance(mixed_type, xsd.Message)

  mixed_fields = {f.name: f for f in mixed_type.get_fields()}
  assert "value" in mixed_fields

  value_field = mixed_fields["value"]
  assert value_field.proto_type is xsd.AtomicType.STRING


_USER_DEFINED_SIMPLE_TYPE_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">

  <xs:simpleType name="MyStringType">
    <xs:restriction base="xs:string">
      <xs:maxLength value="10"/>
    </xs:restriction>
  </xs:simpleType>

  <xs:simpleType name="MyRestrictedString">
    <xs:restriction base="MyStringType">
      <xs:minLength value="2"/>
    </xs:restriction>
  </xs:simpleType>

  <xs:simpleType name="UnionType">
    <xs:union memberTypes="xs:int MyRestrictedString"/>
  </xs:simpleType>

  <xs:complexType name="RootType">
    <xs:sequence>
      <xs:element name="restricted_string" type="MyRestrictedString"/>
      <xs:element name="union_field" type="UnionType"/>
    </xs:sequence>
  </xs:complexType>

  <xs:element name="root" type="RootType" />
</xs:schema>
"""


def test_user_defined_simple_types() -> None:
  type_defs = xsd.process_xsd(io.StringIO(_USER_DEFINED_SIMPLE_TYPE_XSD))
  type_defs_by_name = {t.name: t for t in type_defs if t.name}

  root_type = type_defs_by_name["RootType"]
  fields = {f.name: f for f in root_type.get_fields()}
  assert "union_field" in fields
  union_field = fields["union_field"]
  assert union_field.proto_type is xsd.AtomicType.STRING
