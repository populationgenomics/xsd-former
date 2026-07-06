import types

from google.protobuf import text_format
from lxml import etree

from tests.xsdformer import conftest
from xsdformer.xsd import xsd


def test_book_role_converter(
    book_converter: types.ModuleType,
) -> None:
    book_pb2 = book_converter.book_pb2
    assert hasattr(book_converter, 'Role')
    assert book_converter.Role('editor') == book_pb2.Role.ROLE_EDITOR


_BOOK_XML = """
<book id="bk101" status="new">
  <authors>
    <author>
      <name>Gambardella, Matthew</name>
      <role>author</role>
    </author>
  </authors>
  <title>XML Developer's Guide</title>
  <isbn>0-07-212679-9</isbn>
  <metadata>
    <extra>
        <some_tag>some value</some_tag>
    </extra>
  </metadata>
</book>
"""

_BOOK_PROTO = """
id: "bk101"
status: STATUS_NEW
authors {
  author {
    name: "Gambardella, Matthew"
    role: ROLE_AUTHOR
  }
}
title: "XML Developer's Guide"
isbn: "0-07-212679-9"
"""


def test_xml_to_proto(
    book_converter: types.ModuleType,
) -> None:
    root = etree.XML(_BOOK_XML, parser=None)
    proto_book = book_converter.Book(root)

    expected_metadata = etree.tostring(root.find('metadata')).decode('utf-8')
    assert proto_book.metadata == expected_metadata
    proto_book.ClearField('metadata')
    book_pb2 = book_converter.book_pb2
    expected_book = text_format.Parse(_BOOK_PROTO, book_pb2.Book())

    assert proto_book == expected_book


_UNION_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">

  <xs:simpleType name="IntOrString">
    <xs:union memberTypes="xs:int xs:string"/>
  </xs:simpleType>

  <xs:element name="root">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="value" type="IntOrString"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>

</xs:schema>
"""


def test_xsd_union(
    py_converter_module_factory: conftest.PyConverterModuleFactory,
) -> None:
    # TODO: should unions of different atomic types be represented as protobuf oneof?
    #   pro:
    #     * more accurately reflects the original data.
    #     * more compact representation.
    #     * better typesafety.
    #   con:
    #     * more awkward to access. value_int / value_string - need to know which
    #       one.
    #     * have to synthesize field name suffixes.
    union_module = py_converter_module_factory(
        _UNION_XSD,
        proto_namespace='union',
        py_module='union',
    )
    union_pb2 = union_module.union_pb2
    # Test with an integer value
    xml_int = '<root><value>123</value></root>'
    root_int = etree.XML(xml_int, parser=None)
    proto_int = union_module.Root(root_int)
    expected_proto_int = text_format.Parse('value: "123"', union_pb2.Root())
    assert proto_int == expected_proto_int

    # Test with a string value
    xml_string = '<root><value>hello</value></root>'
    root_string = etree.XML(xml_string, parser=None)
    proto_string = union_module.Root(root_string)
    expected_proto_string = text_format.Parse('value: "hello"', union_pb2.Root())
    assert proto_string == expected_proto_string


_CHOICE_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">

  <xs:complexType name="CreditCardType">
    <xs:sequence>
      <xs:element name="cardNumber" type="xs:string"/>
      <xs:element name="expiryDate" type="xs:string"/>
    </xs:sequence>
  </xs:complexType>

  <xs:complexType name="BankAccountType">
    <xs:sequence>
      <xs:element name="accountNumber" type="xs:string"/>
      <xs:element name="bankName" type="xs:string"/>
    </xs:sequence>
  </xs:complexType>

  <xs:complexType name="PaymentMethod">
    <xs:choice>
      <xs:element name="creditCard" type="CreditCardType"/>
      <xs:element name="bankAccount" type="BankAccountType"/>
    </xs:choice>
  </xs:complexType>

  <xs:element name="payment" type="PaymentMethod"/>

</xs:schema>
"""

_CHOICE_XML_CREDIT_CARD = """
<payment>
  <creditCard>
    <cardNumber>1234-5678-9012-3456</cardNumber>
    <expiryDate>12/25</expiryDate>
  </creditCard>
</payment>
"""

_CHOICE_XML_BANK_ACCOUNT = """
<payment>
  <bankAccount>
    <accountNumber>987654321</accountNumber>
    <bankName>MyBank</bankName>
  </bankAccount>
</payment>
"""

_CHOICE_PROTO_CREDIT_CARD = """
credit_card {
  card_number: "1234-5678-9012-3456"
  expiry_date: "12/25"
}
"""

_CHOICE_PROTO_BANK_ACCOUNT = """
bank_account {
  account_number: "987654321"
  bank_name: "MyBank"
}
"""


def test_xsd_choice(
    py_converter_module_factory: conftest.PyConverterModuleFactory,
) -> None:
    choice_module = py_converter_module_factory(
        _CHOICE_XSD,
        proto_namespace='choice',
        py_module='choice',
    )
    choice_pb2 = choice_module.choice_pb2

    # Test with credit card
    root_cc = etree.XML(_CHOICE_XML_CREDIT_CARD, parser=None)
    proto_cc = choice_module.PaymentMethod(root_cc)
    expected_proto_cc = text_format.Parse(
        _CHOICE_PROTO_CREDIT_CARD,
        choice_pb2.PaymentMethod(),
    )
    assert proto_cc == expected_proto_cc

    # Test with bank account
    root_ba = etree.XML(_CHOICE_XML_BANK_ACCOUNT, parser=None)
    proto_ba = choice_module.PaymentMethod(root_ba)
    expected_proto_ba = text_format.Parse(
        _CHOICE_PROTO_BANK_ACCOUNT,
        choice_pb2.PaymentMethod(),
    )
    assert proto_ba == expected_proto_ba


_CHOICE_OCCURS_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">

  <xs:complexType name="CreditCardType">
    <xs:sequence>
      <xs:element name="cardNumber" type="xs:string"/>
      <xs:element name="expiryDate" type="xs:string"/>
    </xs:sequence>
  </xs:complexType>

  <xs:complexType name="BankAccountType">
    <xs:sequence>
      <xs:element name="accountNumber" type="xs:string"/>
      <xs:element name="bankName" type="xs:string"/>
    </xs:sequence>
  </xs:complexType>

  <xs:complexType name="PaymentMethod">
    <xs:choice minOccurs="0" maxOccurs="unbounded">
      <xs:element name="creditCard" type="CreditCardType"/>
      <xs:element name="bankAccount" type="BankAccountType"/>
    </xs:choice>
  </xs:complexType>

  <xs:element name="payment" type="PaymentMethod"/>

</xs:schema>
"""

_CHOICE_OCCURS_XML_EMPTY = """
<payment/>
"""

_CHOICE_OCCURS_XML_ONE = """
<payment>
  <creditCard>
    <cardNumber>1234-5678-9012-3456</cardNumber>
    <expiryDate>12/25</expiryDate>
  </creditCard>
</payment>
"""

_CHOICE_OCCURS_XML_TWO = """
<payment>
  <creditCard>
    <cardNumber>1234-5678-9012-3456</cardNumber>
    <expiryDate>12/25</expiryDate>
  </creditCard>
  <bankAccount>
    <accountNumber>987654321</accountNumber>
    <bankName>MyBank</bankName>
  </bankAccount>
</payment>
"""

_CHOICE_OCCURS_PROTO_EMPTY = """
"""

_CHOICE_OCCURS_PROTO_ONE = """
credit_card {
  card_number: "1234-5678-9012-3456"
  expiry_date: "12/25"
}
"""

_CHOICE_OCCURS_PROTO_TWO = """
credit_card {
  card_number: "1234-5678-9012-3456"
  expiry_date: "12/25"
}
bank_account {
  account_number: "987654321"
  bank_name: "MyBank"
}
"""


def test_xsd_choice_occurs(
    py_converter_module_factory: conftest.PyConverterModuleFactory,
) -> None:
    choice_module = py_converter_module_factory(
        _CHOICE_OCCURS_XSD,
        proto_namespace='choice_occurs',
        py_module='choice_occurs',
    )
    choice_pb2 = choice_module.choice_occurs_pb2

    # Test with empty
    root_empty = etree.XML(_CHOICE_OCCURS_XML_EMPTY, parser=None)
    proto_empty = choice_module.PaymentMethod(root_empty)
    expected_proto_empty = text_format.Parse(
        _CHOICE_OCCURS_PROTO_EMPTY,
        choice_pb2.PaymentMethod(),
    )
    assert proto_empty == expected_proto_empty

    # Test with one
    root_one = etree.XML(_CHOICE_OCCURS_XML_ONE, parser=None)
    proto_one = choice_module.PaymentMethod(root_one)
    expected_proto_one = text_format.Parse(
        _CHOICE_OCCURS_PROTO_ONE,
        choice_pb2.PaymentMethod(),
    )
    assert proto_one == expected_proto_one

    # Test with two
    root_two = etree.XML(_CHOICE_OCCURS_XML_TWO, parser=None)
    proto_two = choice_module.PaymentMethod(root_two)
    expected_proto_two = text_format.Parse(
        _CHOICE_OCCURS_PROTO_TWO,
        choice_pb2.PaymentMethod(),
    )
    assert proto_two == expected_proto_two


_DATE_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="root">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="date" type="xs:date"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>
"""

_DATE_XML = """
<root>
  <date>2025-07-31Z</date>
</root>
"""

_DATE_PROTO = """
date {
  seconds: 1753920000
}
"""


def test_xsd_date(
    py_converter_module_factory: conftest.PyConverterModuleFactory,
) -> None:
    date_module = py_converter_module_factory(
        _DATE_XSD,
        proto_namespace='date',
        py_module='date',
    )
    date_pb2 = date_module.date_pb2

    root = etree.XML(_DATE_XML, parser=None)
    proto = date_module.Root(root)
    expected_proto = text_format.Parse(_DATE_PROTO, date_pb2.Root())
    assert proto == expected_proto


_MIXED_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="root">
    <xs:complexType mixed="true">
    </xs:complexType>
  </xs:element>
</xs:schema>
"""

_MIXED_XML = """
<root>
  some text
</root>
"""

_MIXED_PROTO = r"""
value: "\n  some text\n"
"""


def test_mixed_content(
    py_converter_module_factory: conftest.PyConverterModuleFactory,
) -> None:
    mixed_module = py_converter_module_factory(
        _MIXED_XSD,
        proto_namespace='mixed',
        py_module='mixed',
    )
    mixed_pb2 = mixed_module.mixed_pb2

    root = etree.XML(_MIXED_XML, parser=None)
    proto = mixed_module.Root(root)
    expected_proto = text_format.Parse(_MIXED_PROTO, mixed_pb2.Root())
    assert proto == expected_proto


_COMPLEX_ENUM_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">

  <xs:simpleType name="StatusType">
    <xs:restriction base="xs:string">
      <xs:enumeration value="active"/>
      <xs:enumeration value="inactive"/>
    </xs:restriction>
  </xs:simpleType>

  <xs:complexType name="Status">
    <xs:simpleContent>
      <xs:extension base="StatusType">
        <xs:attribute name="code" type="xs:int"/>
      </xs:extension>
    </xs:simpleContent>
  </xs:complexType>

  <xs:element name="status" type="Status"/>

</xs:schema>
"""

_COMPLEX_ENUM_XML = '<status code="1">active</status>'

_COMPLEX_ENUM_PROTO = """
value: STATUS_TYPE_ACTIVE
code: 1
"""


def test_complex_enum(
    py_converter_module_factory: conftest.PyConverterModuleFactory,
) -> None:
    module = py_converter_module_factory(
        _COMPLEX_ENUM_XSD,
        proto_namespace='complex_enum',
        py_module='complex_enum',
    )
    pb2_module = module.complex_enum_pb2

    root = etree.XML(_COMPLEX_ENUM_XML, parser=None)
    proto = module.Status(root)
    expected_proto = text_format.Parse(_COMPLEX_ENUM_PROTO, pb2_module.Status())
    assert proto == expected_proto


_MAP_TYPE_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">

  <xs:element name="infon">
    <xs:complexType mixed="true">
      <xs:attribute name="key" type="xs:string" use="required"/>
    </xs:complexType>
  </xs:element>

  <xs:element name="document">
    <xs:complexType>
      <xs:sequence>
        <xs:element minOccurs="0" maxOccurs="unbounded" ref="infon"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>

</xs:schema>
"""

_MAP_TYPE_XML = """
<document>
  <infon key="foo">bar</infon>
  <infon key="baz">qux</infon>
</document>
"""

_MAP_TYPE_PROTO = """
infon { key: "foo" value: "bar" }
infon { key: "baz" value: "qux" }
"""


def test_map_type(
    py_converter_module_factory: conftest.PyConverterModuleFactory,
) -> None:
    module = py_converter_module_factory(
        _MAP_TYPE_XSD,
        proto_namespace='map_type',
        py_module='map_type',
        config=xsd.Config(
            map_overrides=(
                xsd.MapOverrideConfig(
                    map_type=('Infon',),
                    key_field='key',
                    value_field='value',
                ),
            ),
        ),
    )
    pb2_module = module.map_type_pb2
    assert hasattr(pb2_module, 'Document')
    assert not hasattr(pb2_module, 'Infon')

    root = etree.XML(_MAP_TYPE_XML, parser=None)
    proto = module.Document(root)
    expected_proto = text_format.Parse(_MAP_TYPE_PROTO, pb2_module.Document())
    assert proto == expected_proto
