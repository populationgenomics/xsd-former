import types
from collections.abc import Iterable

from google.protobuf import descriptor_pb2

from tests.xsdformer import conftest


def test_generate_protobufs(book_pb2: types.ModuleType) -> None:
    assert hasattr(book_pb2, "Book")
    assert hasattr(book_pb2, "Author")
    assert hasattr(book_pb2.Role, "ROLE_AUTHOR")


def _find_location(
    desc: descriptor_pb2.FileDescriptorProto,
    path: Iterable[int],
) -> descriptor_pb2.SourceCodeInfo.Location:
    path_tuple = tuple(path)
    return next(loc for loc in desc.source_code_info.location if tuple(loc.path) == path_tuple)


def test_documentation_in_generated_protobuf(
    pb2_module_factory: conftest.Pb2ModuleFactory,
) -> None:
    xsd_str = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:complexType name="TestType">
    <xs:sequence>
      <xs:element name="name">
        <xs:annotation>
          <xs:documentation>This is a documented field.</xs:documentation>
        </xs:annotation>
        <xs:simpleType>
          <xs:restriction base="xs:string" />
        </xs:simpleType>
      </xs:element>
    </xs:sequence>
  </xs:complexType>
  <xs:element name="root" type="TestType" />
</xs:schema>
"""
    _, desc_path = pb2_module_factory(xsd_str, namespace="test")

    desc = descriptor_pb2.FileDescriptorSet()
    desc.ParseFromString(desc_path.read_bytes())
    assert len(desc.file) == 1
    file_desc = desc.file[0]

    message_type = file_desc.message_type[0]
    assert message_type.name == "TestType"
    field = message_type.field[0]
    assert field.name == "name"
    # Path to the 'name' field within the FileDescriptorProto.
    # 4: message_type field
    # 0: index of the message type
    # 2: field field
    # 0: index of the field
    location = _find_location(file_desc, [4, 0, 2, 0])
    assert location.leading_comments.strip() == "This is a documented field."
