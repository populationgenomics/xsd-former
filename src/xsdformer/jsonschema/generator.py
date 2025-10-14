import json
import pathlib
import subprocess
import sys
import tempfile
from typing import Any

from google.protobuf import descriptor, descriptor_pb2, descriptor_pool

from xsdformer.protobuf import generator as proto_generator
from xsdformer.xsd import xsd


class _JsonSchemaFromDesc:
  """Generates a JSON schema from a FileDescriptorSet."""

  def __init__(self, descriptor_set: descriptor_pb2.FileDescriptorSet) -> None:
    """Initializes the generator.

    Args:
        descriptor_set: The FileDescriptorSet to generate the schema from.
    """
    self._pool = descriptor_pool.DescriptorPool()
    from google.protobuf import timestamp_pb2

    timestamp_fdp = descriptor_pb2.FileDescriptorProto()
    timestamp_fdp.ParseFromString(timestamp_pb2.DESCRIPTOR.serialized_pb)
    self._pool.Add(timestamp_fdp)

    self._source_info: dict[str, dict[tuple, Any]] = {}
    self._fdp_map = {fdp.name: fdp for fdp in descriptor_set.file}
    for fdp in descriptor_set.file:
      self._pool.Add(fdp)
      self._source_info[fdp.name] = {
        tuple(loc.path): loc for loc in fdp.source_code_info.location
      }

    self._definitions: dict[str, dict[str, Any]] = {}

  def generate(self, message_name: str) -> dict:
    """Generates the JSON schema for a given message.

    Args:
        message_name: The fully qualified name of the message to generate the
          schema for.

    Returns:
        A dictionary representing the JSON schema.
    """
    message_descriptor = self._pool.FindMessageTypeByName(message_name)
    if not message_descriptor:
      raise ValueError(f"Message '{message_name}' not found in descriptor set.")

    # Start the conversion
    self._convert_message_to_schema(message_descriptor)

    return {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "$ref": f"#/definitions/{message_descriptor.full_name}",
      "definitions": self._definitions,
    }

  def _get_comment(
    self,
    desc: descriptor.Descriptor
    | descriptor.FieldDescriptor
    | descriptor.EnumDescriptor
    | descriptor.EnumValueDescriptor,
  ) -> str | None:
    path: list[int] = []
    file_name = ""
    if isinstance(desc, descriptor.FieldDescriptor):
      if desc.containing_type.GetOptions().map_entry:
        return None  # No comments for map entry fields
      path = self._get_field_path(desc)
      file_name = desc.containing_type.file.name
    elif isinstance(desc, descriptor.Descriptor):
      path = self._get_message_path(desc)
      file_name = desc.file.name
    elif isinstance(desc, descriptor.EnumDescriptor):
      path = self._get_enum_path(desc)
      file_name = desc.file.name
    elif isinstance(desc, descriptor.EnumValueDescriptor):
      path = self._get_enum_value_path(desc)
      file_name = desc.type.file.name

    if file_name in self._source_info and tuple(path) in self._source_info[file_name]:
      loc = self._source_info[file_name][tuple(path)]
      return loc.leading_comments.strip()
    return None

  def _get_message_path(self, desc: descriptor.Descriptor) -> list[int]:
    if desc.containing_type:
      path = self._get_message_path(desc.containing_type)
      containing_type_dp = self._find_descriptor_proto(desc.containing_type)
      if containing_type_dp:
        for i, nested_type in enumerate(containing_type_dp.nested_type):
          if nested_type.name == desc.name:
            path.extend([descriptor_pb2.DescriptorProto.NESTED_TYPE_FIELD_NUMBER, i])
            return path
    else:
      fdp = self._fdp_map[desc.file.name]
      for i, message_type in enumerate(fdp.message_type):
        if message_type.name == desc.name:
          return [descriptor_pb2.FileDescriptorProto.MESSAGE_TYPE_FIELD_NUMBER, i]
    return []

  def _get_field_path(self, desc: descriptor.FieldDescriptor) -> list[int]:
    path = self._get_message_path(desc.containing_type)
    containing_type_dp = self._find_descriptor_proto(desc.containing_type)
    if containing_type_dp:
      for i, field in enumerate(containing_type_dp.field):
        if field.name == desc.name:
          path.extend([descriptor_pb2.DescriptorProto.FIELD_FIELD_NUMBER, i])
          return path
    return []

  def _get_enum_path(self, desc: descriptor.EnumDescriptor) -> list[int]:
    if desc.containing_type:
      path = self._get_message_path(desc.containing_type)
      containing_type_dp = self._find_descriptor_proto(desc.containing_type)
      if containing_type_dp:
        for i, enum_type in enumerate(containing_type_dp.enum_type):
          if enum_type.name == desc.name:
            path.extend([descriptor_pb2.DescriptorProto.ENUM_TYPE_FIELD_NUMBER, i])
            return path
    else:
      fdp = self._fdp_map[desc.file.name]
      for i, enum_type in enumerate(fdp.enum_type):
        if enum_type.name == desc.name:
          return [descriptor_pb2.FileDescriptorProto.ENUM_TYPE_FIELD_NUMBER, i]
    return []

  def _get_enum_value_path(self, desc: descriptor.EnumValueDescriptor) -> list[int]:
    path = self._get_enum_path(desc.type)
    edp = self._find_enum_descriptor_proto(desc.type)
    if edp:
      for i, value in enumerate(edp.value):
        if value.name == desc.name:
          path.extend([descriptor_pb2.EnumDescriptorProto.VALUE_FIELD_NUMBER, i])
          return path
    return []

  def _find_descriptor_proto(
    self,
    desc: descriptor.Descriptor,
  ) -> descriptor_pb2.DescriptorProto | None:
    fdp = self._fdp_map[desc.file.name]

    name_parts = desc.full_name.split(".")
    if fdp.package:
      name_parts = desc.full_name.replace(fdp.package + ".", "").split(".")

    def find_nested(
      parent_dp: descriptor_pb2.DescriptorProto,
      parts: list[str],
    ) -> descriptor_pb2.DescriptorProto | None:
      if not parts:
        return parent_dp
      target_name = parts[0]
      for nested_dp in parent_dp.nested_type:
        if nested_dp.name == target_name:
          return find_nested(nested_dp, parts[1:])
      return None

    for dp in fdp.message_type:
      if dp.name == name_parts[0]:
        found = find_nested(dp, name_parts[1:])
        if found:
          return found
    return None

  def _find_enum_descriptor_proto(
    self,
    desc: descriptor.EnumDescriptor,
  ) -> descriptor_pb2.EnumDescriptorProto | None:
    fdp = self._fdp_map[desc.file.name]
    name_parts = desc.full_name.split(".")
    if fdp.package:
      name_parts = desc.full_name.replace(fdp.package + ".", "").split(".")

    if not desc.containing_type:
      for edp in fdp.enum_type:
        if edp.name == name_parts[0]:
          return edp
      return None

    for dp in fdp.message_type:
      if dp.name == name_parts[0]:
        found = self._find_enum_in_message(dp, name_parts[1:])
        if found:
          return found
    return None

  def _find_enum_in_message(
    self,
    parent_dp: descriptor_pb2.DescriptorProto,
    parts: list[str],
  ) -> descriptor_pb2.EnumDescriptorProto | None:
    if not parts:
      return None

    target_name = parts[0]
    if len(parts) == 1:
      for edp in parent_dp.enum_type:
        if edp.name == target_name:
          return edp
      return None

    for nested_dp in parent_dp.nested_type:
      if nested_dp.name == target_name:
        return self._find_enum_in_message(nested_dp, parts[1:])
    return None

  def _convert_message_to_schema(
    self,
    message_descriptor: descriptor.Descriptor,
  ) -> dict:
    message_name = message_descriptor.full_name
    if message_name in self._definitions:
      return {"$ref": f"#/definitions/{message_name}"}

    properties = {}
    for field in message_descriptor.fields:
      properties[field.json_name] = self._convert_field_to_schema(field)

    schema: dict[str, Any] = {
      "type": "object",
      "properties": properties,
    }

    comment = self._get_comment(message_descriptor)
    if comment:
      schema["description"] = comment

    self._definitions[message_name] = schema

    # Recursively convert nested messages
    for field in message_descriptor.fields:
      if field.type == descriptor.FieldDescriptor.TYPE_MESSAGE:
        self._convert_message_to_schema(field.message_type)

    return schema

  def _convert_field_to_schema(self, field: descriptor.FieldDescriptor) -> dict:
    if field.is_repeated:
      if field.message_type and field.message_type.GetOptions().map_entry:
        value_schema = self._get_field_schema(
          field.message_type.fields_by_name["value"],
        )
        return {"type": "object", "additionalProperties": value_schema}
      schema: dict[str, Any] = {
        "type": "array",
        "items": self._get_field_schema(field),
      }
    else:
      schema = self._get_field_schema(field)

    comment = self._get_comment(field)
    if comment:
      schema["description"] = comment

    return schema

  def _get_field_schema(self, field: descriptor.FieldDescriptor) -> dict:
    field_type = field.type

    if field_type == descriptor.FieldDescriptor.TYPE_ENUM:
      return self._get_enum_schema(field)

    type_map: dict[int, dict[str, Any]] = {
      descriptor.FieldDescriptor.TYPE_DOUBLE: {"type": "number"},
      descriptor.FieldDescriptor.TYPE_FLOAT: {"type": "number"},
      descriptor.FieldDescriptor.TYPE_INT64: {"type": "integer"},
      descriptor.FieldDescriptor.TYPE_UINT64: {"type": "integer"},
      descriptor.FieldDescriptor.TYPE_INT32: {"type": "integer"},
      descriptor.FieldDescriptor.TYPE_FIXED64: {"type": "integer"},
      descriptor.FieldDescriptor.TYPE_FIXED32: {"type": "integer"},
      descriptor.FieldDescriptor.TYPE_UINT32: {"type": "integer"},
      descriptor.FieldDescriptor.TYPE_SFIXED32: {"type": "integer"},
      descriptor.FieldDescriptor.TYPE_SFIXED64: {"type": "integer"},
      descriptor.FieldDescriptor.TYPE_SINT32: {"type": "integer"},
      descriptor.FieldDescriptor.TYPE_SINT64: {"type": "integer"},
      descriptor.FieldDescriptor.TYPE_BOOL: {"type": "boolean"},
      descriptor.FieldDescriptor.TYPE_STRING: {"type": "string"},
      descriptor.FieldDescriptor.TYPE_BYTES: {
        "type": "string",
        "contentEncoding": "base64",
      },
      descriptor.FieldDescriptor.TYPE_MESSAGE: {
        "$ref": f"#/definitions/{field.message_type.full_name}"
        if field.message_type
        else {},
      },
    }
    return type_map.get(field_type, {})

  def _get_enum_schema(self, field: descriptor.FieldDescriptor) -> dict:
    one_of = [
      {"const": value.name, "description": self._get_comment(value)}
      for value in field.enum_type.values  # noqa: PD011 (false positive)
    ]

    schema: dict[str, Any] = {"oneOf": one_of}
    enum_comment = self._get_comment(field.enum_type)
    if enum_comment:
      schema["description"] = enum_comment
    return schema


def generate(
  namespace: str,
  type_defs: tuple[xsd.TypeDefinition, ...],
  main_message: str,
) -> str:
  """Generates a JSON schema from XSD type definitions."""
  proto_def = "\n".join(proto_generator.generate(namespace, type_defs))

  with tempfile.TemporaryDirectory() as tmpdir:
    tmp_path = pathlib.Path(tmpdir)
    proto_path = tmp_path / f"{namespace}.proto"
    proto_path.write_text(proto_def)

    desc_path = tmp_path / f"{namespace}.desc"

    import importlib.util

    spec = importlib.util.find_spec("google.protobuf.timestamp_pb2")
    if not spec or not spec.origin:
      raise ImportError("google.protobuf.timestamp_pb2 not found")

    proto_include_path = pathlib.Path(spec.origin).parent.parent

    subprocess.run(  # noqa: S603
      [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"--proto_path={tmp_path}",
        f"--proto_path={proto_include_path}",
        f"--descriptor_set_out={desc_path}",
        "--include_source_info",
        str(proto_path.relative_to(tmp_path)),
      ],
      check=True,
    )

    with open(desc_path, "rb") as f:
      descriptor_set = descriptor_pb2.FileDescriptorSet.FromString(f.read())

  schema_generator = _JsonSchemaFromDesc(descriptor_set)
  fully_qualified_main_message = f"{namespace}.{main_message}"
  schema = schema_generator.generate(fully_qualified_main_message)

  return json.dumps(schema, indent=2)
