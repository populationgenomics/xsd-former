import io
import json

from xsdformer.jsonschema import generator as jsonschema_generator
from xsdformer.xsd import xsd

_TEST_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:complexType name="Address">
    <xs:sequence>
      <xs:element name="street" type="xs:string" />
      <xs:element name="city" type="xs:string" />
    </xs:sequence>
  </xs:complexType>
  <xs:complexType name="Person">
    <xs:sequence>
      <xs:element name="name" type="xs:string" />
      <xs:element name="address" type="Address" />
      <xs:element name="timestamp" type="xs:dateTime" />
    </xs:sequence>
  </xs:complexType>
  <xs:element name="person" type="Person" />
</xs:schema>
"""


def test_generate_schema_from_xsd() -> None:
    type_defs = xsd.process_xsd(io.StringIO(_TEST_XSD))
    schema_str = jsonschema_generator.generate("test", type_defs, "Person")
    schema = json.loads(schema_str)

    expected_schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$ref": "#/definitions/test.Person",
        "definitions": {
            "test.Person": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "address": {"$ref": "#/definitions/test.Address"},
                    "timestamp": {
                        "type": "string",
                        "format": "date-time",
                    },
                },
            },
            "test.Address": {
                "type": "object",
                "properties": {
                    "street": {"type": "string"},
                    "city": {"type": "string"},
                },
            },
        },
    }

    assert schema == expected_schema


_PRESERVING_FIELD_NAME_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:complexType name="TestMessage">
    <xs:sequence>
      <xs:element name="some_field" type="xs:string" />
    </xs:sequence>
  </xs:complexType>
  <xs:element name="test_message" type="TestMessage" />
</xs:schema>
"""


def test_generate_schema_with_preserving_proto_field_name() -> None:
    type_defs = xsd.process_xsd(io.StringIO(_PRESERVING_FIELD_NAME_XSD))
    schema_str = jsonschema_generator.generate(
        "test_preserving",
        type_defs,
        "TestMessage",
        preserving_proto_field_name=True,
    )
    schema = json.loads(schema_str)

    expected_schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$ref": "#/definitions/test_preserving.TestMessage",
        "definitions": {
            "test_preserving.TestMessage": {
                "type": "object",
                "properties": {
                    "some_field": {"type": "string"},
                },
            },
        },
    }

    assert schema == expected_schema
