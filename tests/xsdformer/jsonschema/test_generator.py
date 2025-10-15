import datetime
import io
import json
import pathlib
import tempfile

import jsonschema
import pytest
from google.protobuf import json_format

from tests.xsdformer import conftest
from xsdformer.jsonschema import generator
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
    schema_str = generator.generate("test", type_defs, "Person")
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
    schema_str = generator.generate(
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


def test_generate_from_proto() -> None:
    proto_content = """
        syntax = "proto3";

        package testpkg;

        import "google/protobuf/timestamp.proto";

        message Person {
            string name = 1;
            int32 id = 2;
            string email = 3;
            google.protobuf.Timestamp created_at = 4;
        }
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = pathlib.Path(tmpdir)
        proto_path = tmp_path / "test.proto"
        proto_path.write_text(proto_content)

        json_schema_str = generator.generate_from_proto(
            proto_path=proto_path,
            namespace="testpkg",
            main_message="Person",
        )

    schema = json.loads(json_schema_str)

    # Assert the schema structure is correct
    assert schema["$ref"] == "#/definitions/testpkg.Person"
    person_def = schema["definitions"]["testpkg.Person"]
    assert person_def["type"] == "object"
    assert person_def["properties"]["name"] == {"type": "string"}
    assert person_def["properties"]["id"] == {"type": "integer"}
    assert person_def["properties"]["email"] == {"type": "string"}
    assert person_def["properties"]["createdAt"] == {
        "type": "string",
        "format": "date-time",
    }

    # Validate a correct payload against the schema
    person_instance = {
        "name": "John Doe",
        "id": 123,
        "email": "john.doe@example.com",
        "createdAt": "2024-01-01T00:00:00Z",
    }
    jsonschema.validate(instance=person_instance, schema=schema)

    # Assert that an incorrect payload fails validation
    person_instance_invalid = {"name": "Jane Doe", "id": "not-an-integer"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=person_instance_invalid, schema=schema)


def test_generate_from_proto_include_all() -> None:
    """Tests the --include-all flag to include all messages from a proto file."""
    proto_content = """
        syntax = "proto3";

        package testall;

        message MessageA {
            string field_a = 1;
        }

        message MessageB {
            string field_b = 1;
        }
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = pathlib.Path(tmpdir)
        proto_path = tmp_path / "test.proto"
        proto_path.write_text(proto_content)

        # Test default behavior (include_all=False)
        json_schema_str_default = generator.generate_from_proto(
            proto_path=proto_path,
            namespace="testall",
            main_message="MessageA",
            include_all=False,
        )
        schema_default = json.loads(json_schema_str_default)

        assert "testall.MessageA" in schema_default["definitions"]
        assert "testall.MessageB" not in schema_default["definitions"]

        # Test with include_all=True
        json_schema_str_all = generator.generate_from_proto(
            proto_path=proto_path,
            namespace="testall",
            main_message="MessageA",
            include_all=True,
        )
        schema_all = json.loads(json_schema_str_all)

        assert "testall.MessageA" in schema_all["definitions"]
        assert "testall.MessageB" in schema_all["definitions"]


@pytest.mark.parametrize("preserving_proto_field_name", [True, False])
def test_xsd_to_json_schema_e2e(
    pb2_module_factory: conftest.Pb2ModuleFactory,
    preserving_proto_field_name: bool,
) -> None:
    """An end-to-end test verifying a protobuf's JSON output against the JSON schema."""
    # 1. Get the type definitions from the test XSD
    namespace = "person"
    main_message = "Person"
    type_defs = xsd.process_xsd(io.StringIO(_TEST_XSD))

    # 2. Generate the JSON Schema from the same XSD, using the parameter
    schema_str = generator.generate(
        namespace=namespace,
        type_defs=type_defs,
        main_message=main_message,
        preserving_proto_field_name=preserving_proto_field_name,
    )
    schema = json.loads(schema_str)

    # 3. Create a protobuf instance using a generated protobuf module
    person_pb2, _ = pb2_module_factory(_TEST_XSD, namespace=namespace)
    person_instance = person_pb2.Person(
        name="John Doe",
        address=person_pb2.Address(street="123 Main St", city="Anytown"),
    )
    person_instance.timestamp.FromDatetime(datetime.datetime.now(datetime.UTC))

    # 4. Convert the protobuf instance to a JSON dictionary, using the parameter
    json_dict = json.loads(
        json_format.MessageToJson(
            person_instance,
            preserving_proto_field_name=preserving_proto_field_name,
        ),
    )

    # 5. Validate the JSON against the JSON Schema
    jsonschema.validate(instance=json_dict, schema=schema)
