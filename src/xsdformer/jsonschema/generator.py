import json
import pathlib
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from google.protobuf import descriptor, descriptor_pb2, descriptor_pool, timestamp_pb2

from xsdformer.protobuf import generator as proto_generator
from xsdformer.xsd import xsd

_INTEGER_FIELD_TYPES = frozenset(
    {
        descriptor.FieldDescriptor.TYPE_INT64,
        descriptor.FieldDescriptor.TYPE_UINT64,
        descriptor.FieldDescriptor.TYPE_INT32,
        descriptor.FieldDescriptor.TYPE_FIXED64,
        descriptor.FieldDescriptor.TYPE_FIXED32,
        descriptor.FieldDescriptor.TYPE_UINT32,
        descriptor.FieldDescriptor.TYPE_SFIXED32,
        descriptor.FieldDescriptor.TYPE_SFIXED64,
        descriptor.FieldDescriptor.TYPE_SINT32,
        descriptor.FieldDescriptor.TYPE_SINT64,
    },
)

_FLOAT_FIELD_TYPES = frozenset(
    {
        descriptor.FieldDescriptor.TYPE_FLOAT,
        descriptor.FieldDescriptor.TYPE_DOUBLE,
    },
)


DescriptorT = TypeVar(
    "DescriptorT",
    bound=descriptor_pb2.DescriptorProto | descriptor_pb2.EnumDescriptorProto,
)


def _find_descriptor(
    fdp: descriptor_pb2.FileDescriptorProto,
    name_parts: list[str],
    find_nested: Callable[
        [descriptor_pb2.DescriptorProto, list[str]],
        DescriptorT | None,
    ],
) -> DescriptorT | None:
    for dp in fdp.message_type:
        if dp.name == name_parts[0]:
            found = find_nested(dp, name_parts[1:])
            if found:
                return found
    return None


class _JsonSchemaFromDesc:
    """Generates a JSON schema from a FileDescriptorSet."""

    def __init__(
        self,
        descriptor_set: descriptor_pb2.FileDescriptorSet,
        preserving_proto_field_name: bool = False,
    ) -> None:
        """Initializes the generator.

        Args:
            descriptor_set: The FileDescriptorSet to generate the schema from.
            preserving_proto_field_name: If true, use the proto field name for keys
              instead of the json_name.
        """
        self._pool = descriptor_pool.DescriptorPool()
        self._preserving_proto_field_name = preserving_proto_field_name

        timestamp_fdp = descriptor_pb2.FileDescriptorProto()
        timestamp_fdp.ParseFromString(timestamp_pb2.DESCRIPTOR.serialized_pb)
        self._pool.Add(timestamp_fdp)

        self._source_info: dict[str, dict[tuple, Any]] = {}
        self._fdp_map = {fdp.name: fdp for fdp in descriptor_set.file}
        for fdp in descriptor_set.file:
            self._pool.Add(fdp)
            self._source_info[fdp.name] = {tuple(loc.path): loc for loc in fdp.source_code_info.location}

        self._definitions: dict[str, dict[str, Any]] = {}

    def generate_all(self, message_name: str | None) -> dict[str, Any]:
        main_message_descriptor: descriptor.Descriptor | None = None
        for fdp in self._fdp_map.values():
            for msg_proto in fdp.message_type:
                full_name = f"{fdp.package}.{msg_proto.name}" if fdp.package else msg_proto.name
                msg_descriptor = self._pool.FindMessageTypeByName(full_name)
                if full_name == message_name:
                    main_message_descriptor = msg_descriptor
                if msg_descriptor:
                    self._convert_message_to_schema(msg_descriptor)
            for enum_proto in fdp.enum_type:
                full_name = f"{fdp.package}.{enum_proto.name}" if fdp.package else enum_proto.name
                enum_descriptor = self._pool.FindEnumTypeByName(full_name)
                if enum_descriptor:
                    self._convert_enum_to_schema(enum_descriptor)

        schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "definitions": self._definitions,
        }
        if main_message_descriptor:
            schema["$ref"] = f"#/definitions/{main_message_descriptor.full_name}"
        elif message_name:
            raise ValueError(f"Message '{message_name}' not found in descriptor set.")
        return schema

    def generate(self, message_name: str) -> dict:
        """Generates the JSON schema for a given message.

        Args:
            message_name: The fully qualified name of the message to generate the
              schema for.

        Returns:
            A dictionary representing the JSON schema.
        """
        main_message_descriptor = self._pool.FindMessageTypeByName(message_name)
        if not main_message_descriptor:
            raise ValueError(f"Message '{message_name}' not found in descriptor set.")

        self._convert_message_to_schema(main_message_descriptor)

        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "definitions": self._definitions,
            "$ref": f"#/definitions/{main_message_descriptor.full_name}",
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
        try:
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
        except ValueError:
            return None

        if file_name in self._source_info and tuple(path) in self._source_info[file_name]:
            loc = self._source_info[file_name][tuple(path)]
            comment = loc.leading_comments or loc.trailing_comments
            if comment:
                return comment.strip()
        return None

    def _get_message_path(self, desc: descriptor.Descriptor) -> list[int]:
        """Gets the source code path for a message descriptor.

        The path is a list of integers that identifies a specific element within
        the FileDescriptorProto structure. It's used to look up comments from
        the source_code_info. See `SourceCodeInfo.Location.path` in
        `google/protobuf/descriptor.proto` for more details on the format.

        Args:
          desc: The message descriptor to find the path for.

        Returns:
          A list of integers representing the path.

        Raises:
          ValueError: If the path for the descriptor cannot be found.
        """
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
        raise ValueError(f"Could not find source code path for message {desc.full_name}")

    def _get_field_path(self, desc: descriptor.FieldDescriptor) -> list[int]:
        """Gets the source code path for a field descriptor.

        See `_get_message_path` for details on the path format and usage.

        Args:
          desc: The field descriptor to find the path for.

        Returns:
          A list of integers representing the path.

        Raises:
          ValueError: If the path for the descriptor cannot be found.
        """
        path = self._get_message_path(desc.containing_type)
        containing_type_dp = self._find_descriptor_proto(desc.containing_type)
        if containing_type_dp:
            for i, field in enumerate(containing_type_dp.field):
                if field.name == desc.name:
                    path.extend([descriptor_pb2.DescriptorProto.FIELD_FIELD_NUMBER, i])
                    return path
        raise ValueError(f"Could not find source code path for field {desc.full_name}")

    def _get_enum_path(self, desc: descriptor.EnumDescriptor) -> list[int]:
        """Gets the source code path for an enum descriptor.

        See `_get_message_path` for details on the path format and usage.

        Args:
          desc: The enum descriptor to find the path for.

        Returns:
          A list of integers representing the path.

        Raises:
          ValueError: If the path for the descriptor cannot be found.
        """
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
        raise ValueError(f"Could not find source code path for enum {desc.full_name}")

    def _get_enum_value_path(self, desc: descriptor.EnumValueDescriptor) -> list[int]:
        """Gets the source code path for an enum value descriptor.

        See `_get_message_path` for details on the path format and usage.

        Args:
          desc: The enum value descriptor to find the path for.

        Returns:
          A list of integers representing the path.

        Raises:
          ValueError: If the path for the descriptor cannot be found.
        """
        path = self._get_enum_path(desc.type)
        edp = self._find_enum_descriptor_proto(desc.type)
        if edp:
            for i, value in enumerate(edp.value):
                if value.name == desc.name:
                    path.extend([descriptor_pb2.EnumDescriptorProto.VALUE_FIELD_NUMBER, i])
                    return path
        raise ValueError(
            f"Could not find source code path for enum value {desc.name}",
        )

    def _get_name_parts(
        self,
        desc: descriptor.Descriptor | descriptor.EnumDescriptor,
    ) -> list[str]:
        """Gets the relative name parts of a descriptor within its file."""
        fdp = self._fdp_map[desc.file.name]
        if fdp.package:
            return desc.full_name.replace(fdp.package + ".", "").split(".")
        return desc.full_name.split(".")

    def _find_descriptor_proto(
        self,
        desc: descriptor.Descriptor,
    ) -> descriptor_pb2.DescriptorProto | None:
        fdp = self._fdp_map[desc.file.name]

        name_parts = self._get_name_parts(desc)

        return _find_descriptor(fdp, name_parts, self._find_nested)

    def _find_enum_descriptor_proto(
        self,
        desc: descriptor.EnumDescriptor,
    ) -> descriptor_pb2.EnumDescriptorProto | None:
        fdp = self._fdp_map[desc.file.name]
        name_parts = self._get_name_parts(desc)

        if not desc.containing_type:
            for edp in fdp.enum_type:
                if edp.name == name_parts[0]:
                    return edp
            return None

        return _find_descriptor(fdp, name_parts, self._find_enum_in_message)

    def _find_nested(
        self,
        parent_dp: descriptor_pb2.DescriptorProto,
        parts: list[str],
    ) -> descriptor_pb2.DescriptorProto | None:
        if not parts:
            return parent_dp
        target_name = parts[0]
        for nested_dp in parent_dp.nested_type:
            if nested_dp.name == target_name:
                return self._find_nested(nested_dp, parts[1:])
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
        if message_name == "google.protobuf.Timestamp":
            return {}
        if message_name in self._definitions:
            return {"$ref": f"#/definitions/{message_name}"}
        properties = {}
        for field in message_descriptor.fields:
            property_name = field.name if self._preserving_proto_field_name else field.json_name
            properties[property_name] = self._convert_field_to_schema(field)

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
        if field.message_type and field.message_type.full_name == "google.protobuf.Timestamp":
            return {"type": "string", "format": "date-time"}
        field_type = field.type

        if field_type in _INTEGER_FIELD_TYPES:
            schema = {"type": "integer"}
        elif field_type in _FLOAT_FIELD_TYPES:
            schema = {"type": "number"}
        elif field_type == descriptor.FieldDescriptor.TYPE_BOOL:
            schema = {"type": "boolean"}
        elif field_type == descriptor.FieldDescriptor.TYPE_STRING:
            schema = {"type": "string"}
        elif field_type == descriptor.FieldDescriptor.TYPE_BYTES:
            schema = {"type": "string", "contentEncoding": "base64"}
        elif field_type == descriptor.FieldDescriptor.TYPE_ENUM:
            schema = self._get_enum_schema(field)
        elif field_type == descriptor.FieldDescriptor.TYPE_MESSAGE:
            schema = self._get_message_schema(field)
        else:
            raise NotImplementedError(f"Unhandled field type: {field_type}")

        return schema

    def _get_message_schema(self, field: descriptor.FieldDescriptor) -> dict:
        if not field.message_type:
            raise ValueError(f"Field '{field.name}' does not have a message type.")
        return {"$ref": f"#/definitions/{field.message_type.full_name}"}

    def _get_enum_schema(self, field: descriptor.FieldDescriptor) -> dict[str, Any]:
        schema: dict[str, Any] = {}
        has_value_descriptions = any(self._get_comment(v) for v in field.enum_type.values)
        if has_value_descriptions:
            one_of = []
            for value in field.enum_type.values:  # (false positive)
                entry: dict[str, str] = {"const": value.name}
                if comment := self._get_comment(value):
                    entry["description"] = comment
                one_of.append(entry)

            schema["oneOf"] = one_of
        else:
            schema["enum"] = [v.name for v in field.enum_type.values]

        enum_comment = self._get_comment(field.enum_type)
        if enum_comment:
            schema["description"] = enum_comment
        return schema


def _generate_schema_from_descriptor_set(  # noqa: PLR0913
    descriptor_set: descriptor_pb2.FileDescriptorSet,
    namespace: str,
    main_message: str | None,
    preserving_proto_field_name: bool = False,
    include_all: bool = False,
    definitions_only: bool = False,
) -> str:
    """Generates a JSON schema from a FileDescriptorSet."""
    schema_generator = _JsonSchemaFromDesc(
        descriptor_set,
        preserving_proto_field_name=preserving_proto_field_name,
    )
    fully_qualified_main_message = f"{namespace}.{main_message}" if main_message else None
    if include_all:
        schema = schema_generator.generate_all(fully_qualified_main_message)
    else:
        if not fully_qualified_main_message:
            raise ValueError(
                "`main_message` is required when `include_all` is False",
            )
        schema = schema_generator.generate(fully_qualified_main_message)
        if definitions_only:
            del schema["$ref"]

    return json.dumps(schema, indent=2)


def _compile_proto_to_descriptor_set(
    proto_path: pathlib.Path,
    include_paths: Sequence[pathlib.Path] = (),
) -> descriptor_pb2.FileDescriptorSet:
    """Compiles a .proto file to a FileDescriptorSet."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = pathlib.Path(tmpdir)
        desc_path = tmp_path / f"{proto_path.stem}.desc"

        protoc_command = [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            f"--proto_path={proto_path.parent}",
        ]
        if include_paths:
            for include_path in include_paths:
                protoc_command.append(f"--proto_path={include_path}")
        protoc_command.extend(
            [
                f"--descriptor_set_out={desc_path}",
                "--include_source_info",
                str(proto_path),
            ],
        )

        subprocess.run(  # noqa: S603
            protoc_command,
            check=True,
        )

        with open(desc_path, "rb") as f:
            descriptor_set = descriptor_pb2.FileDescriptorSet.FromString(f.read())

    return descriptor_set


def generate(
    namespace: str,
    type_defs: tuple[xsd.TypeDefinition, ...],
    main_message: str,
    preserving_proto_field_name: bool = False,
    definitions_only: bool = False,
) -> str:
    """Generates a JSON schema from XSD type definitions."""
    proto_def = "\n".join(proto_generator.generate(namespace, type_defs))
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = pathlib.Path(tmpdir)
        proto_path = tmp_path / f"{namespace}.proto"
        proto_path.write_text(proto_def)
        descriptor_set = _compile_proto_to_descriptor_set(proto_path)
    return _generate_schema_from_descriptor_set(
        descriptor_set,
        namespace,
        main_message,
        preserving_proto_field_name,
        definitions_only=definitions_only,
    )


def generate_from_proto(  # noqa: PLR0913
    proto_path: pathlib.Path,
    namespace: str,
    main_message: str | None,
    preserving_proto_field_name: bool = False,
    include_all: bool = False,
    definitions_only: bool = False,
) -> str:
    """Generates a JSON schema from a .proto file."""
    descriptor_set = _compile_proto_to_descriptor_set(proto_path)
    return _generate_schema_from_descriptor_set(
        descriptor_set,
        namespace,
        main_message,
        preserving_proto_field_name,
        include_all=include_all,
        definitions_only=definitions_only,
    )
