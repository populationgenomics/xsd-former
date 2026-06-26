import io
import types
from collections.abc import Iterable

from google.protobuf import descriptor_pb2

from tests.xsdformer import conftest
from xsdformer.protobuf import generator
from xsdformer.xsd import xsd


def test_generate_protobufs(book_pb2: types.ModuleType) -> None:
    assert hasattr(book_pb2, "Book")
    assert hasattr(book_pb2, "Author")
    assert hasattr(book_pb2.Role, "ROLE_AUTHOR")


def test_proto3_optional_on_singular_scalars_and_enums() -> None:
    """`(0,1)` singular scalar/enum fields get the proto3 `optional` keyword (ADR 0002 R1)."""
    type_defs = xsd.process_xsd(io.StringIO(conftest._BOOK_XSD))
    proto_text = "\n".join(generator.generate("book", type_defs))

    # Optional scalar element, optional `anyType` (-> string), and optional enum
    # attribute all gain presence.
    assert "optional string comment = " in proto_text
    assert "optional string metadata = " in proto_text
    assert "optional Status status = " in proto_text

    # Required scalars, the required ID attribute, and the repeated enum element
    # stay bare: presence would be redundant (required) or wrong (repeated).
    assert "optional string title" not in proto_text
    assert "optional string isbn" not in proto_text
    assert "optional string id" not in proto_text
    assert "optional repeated" not in proto_text
    assert "repeated Role role = " in proto_text


def test_proto3_optional_marked_in_descriptor(
    pb2_module_factory: conftest.Pb2ModuleFactory,
) -> None:
    """The emitted `optional` survives compilation as descriptor field presence."""
    _, desc_path = pb2_module_factory(conftest._BOOK_XSD, namespace="book")
    desc = descriptor_pb2.FileDescriptorSet()
    desc.ParseFromString(desc_path.read_bytes())
    (file_desc,) = desc.file
    (book_msg,) = (m for m in file_desc.message_type if m.name == "Book")
    presence = {f.name: f.proto3_optional for f in book_msg.field}

    assert presence["comment"] is True
    assert presence["metadata"] is True
    assert presence["status"] is True
    assert presence["title"] is False
    assert presence["isbn"] is False
    assert presence["id"] is False


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
