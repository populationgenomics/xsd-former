import json
import pathlib
import tempfile

from xsdformer.jsonschema import generator


def test_generate_from_proto():
    proto_content = '''
        syntax = "proto3";

        package testpkg;

        message Person {
            string name = 1;
            int32 id = 2;
            string email = 3;
        }
    '''
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

    assert schema["$ref"] == "#/definitions/testpkg.Person"
    person_def = schema["definitions"]["testpkg.Person"]
    assert person_def["type"] == "object"
    assert person_def["properties"]["name"] == {"type": "string"}
    assert person_def["properties"]["id"] == {"type": "integer"}
    assert person_def["properties"]["email"] == {"type": "string"}