import contextlib
import importlib.util
import io
import pathlib
import subprocess
import sys
import types
import uuid
from collections.abc import Generator
from typing import Protocol

import pytest

from xsdformer.protobuf import generator
from xsdformer.py import xml_converter
from xsdformer.transforms import TransformConfig, apply_transforms
from xsdformer.xsd import xsd

_BOOK_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:complexType name="author">
    <xs:sequence>
      <xs:element name="name" type="xs:string" />
      <xs:element name="role" type="role" maxOccurs="unbounded" />
    </xs:sequence>
  </xs:complexType>

  <xs:complexType name="authorList">
    <xs:sequence>
      <xs:element name="author" type="author" maxOccurs="unbounded" />
    </xs:sequence>
  </xs:complexType>

  <xs:complexType name="book">
    <xs:sequence>
      <xs:element name="authors" type="authorList" />
      <xs:element name="title" type="xs:string" />
      <xs:element name="isbn" type="xs:string" />
      <xs:element name="comment" type="xs:string" minOccurs="0" />
      <xs:element name="metadata" type="xs:anyType" minOccurs="0" />
    </xs:sequence>
    <xs:attribute name="id" type="xs:ID" use="required" />
    <xs:attribute name="status" type="status" use="optional" />
  </xs:complexType>

  <xs:simpleType name="role">
    <xs:restriction base="xs:string">
      <xs:enumeration value="author" />
      <xs:enumeration value="editor" />
      <xs:enumeration value="reviewer" />
    </xs:restriction>
  </xs:simpleType>

  <xs:simpleType name="status">
    <xs:restriction base="xs:string">
      <xs:enumeration value="new" />
      <xs:enumeration value="used" />
      <xs:enumeration value="out-of-print" />
    </xs:restriction>
  </xs:simpleType>

  <xs:element name="book" type="book" />
</xs:schema>
"""


@contextlib.contextmanager
def _insert_module(module: types.ModuleType) -> Generator[None, None, None]:
    old_module = sys.modules.get(module.__name__)
    sys.modules[module.__name__] = module
    try:
        yield
    finally:
        if old_module is None:
            del sys.modules[module.__name__]
        else:
            sys.modules[module.__name__] = old_module


def _print_code(code: str) -> None:
    for i, line in enumerate(code.split("\n"), start=1):
        print(f"{i:5d}: {line}")


def _compile_proto(
    proto_def: str,
    namespace: str,
    tmp_path: pathlib.Path,
    proto_include_path: pathlib.Path,
) -> tuple[types.ModuleType, pathlib.Path]:
    proto_path = tmp_path / (namespace.replace(".", "_") + ".proto")
    desc_path = tmp_path / (namespace.replace(".", "_") + ".desc.proto")
    python_file = proto_path.name.replace(".proto", "_pb2.py")
    python_module = namespace.replace(".", "_") + "_pb2"
    proto_path.write_text(proto_def)
    subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            f"--proto_path={tmp_path}",
            f"--proto_path={proto_include_path}",
            f"--python_out={tmp_path}",
            f"--descriptor_set_out={desc_path}",
            "--include_source_info",
            str(proto_path.relative_to(tmp_path)),
        ],
        check=True,
    )

    spec = importlib.util.spec_from_file_location(python_module, tmp_path / python_file)
    assert spec is not None
    assert spec.loader is not None
    module_pb2 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module_pb2)
    return module_pb2, desc_path


class Pb2ModuleFactory(Protocol):
    def __call__(
        self,
        xsd_str: str,
        *,
        config: xsd.Config | None = None,
        transform_config: TransformConfig | None = None,
        namespace: str,
    ) -> tuple[types.ModuleType, pathlib.Path]: ...


@pytest.fixture
def pb2_module_factory(tmp_path: pathlib.Path) -> Pb2ModuleFactory:
    # The protoc compiler needs to be able to find the google.protobuf.timestamp_pb2
    # module. We can find its parent directory and add it to the search path.
    spec = importlib.util.find_spec("google.protobuf.timestamp_pb2")
    assert spec is not None
    assert spec.origin is not None
    proto_include_path = pathlib.Path(spec.origin).parent.parent

    def _factory(
        xsd_str: str,
        *,
        config: xsd.Config | None = None,
        transform_config: TransformConfig | None = None,
        namespace: str,
    ) -> tuple[types.ModuleType, pathlib.Path]:
        unique_namespace = f"{namespace}_{uuid.uuid4().hex}"
        type_defs = xsd.process_xsd(io.StringIO(xsd_str), config)
        if transform_config is not None:
            type_defs = apply_transforms(type_defs, transform_config)
        proto_def = "\n".join(generator.generate(unique_namespace, type_defs))
        return _compile_proto(proto_def, unique_namespace, tmp_path, proto_include_path)

    return _factory


class PyConverterModuleFactory(Protocol):
    def __call__(
        self,
        xsd_str: str,
        *,
        config: xsd.Config | None = None,
        transform_config: TransformConfig | None = None,
        proto_namespace: str,
        py_module: str,
    ) -> types.ModuleType: ...


@pytest.fixture
def py_converter_module_factory(
    pb2_module_factory: Pb2ModuleFactory,
) -> PyConverterModuleFactory:
    def _factory(
        xsd_str: str,
        *,
        config: xsd.Config | None = None,
        transform_config: TransformConfig | None = None,
        proto_namespace: str,
        py_module: str,
    ) -> types.ModuleType:
        module_pb2, _ = pb2_module_factory(
            xsd_str,
            config=config,
            transform_config=transform_config,
            namespace=proto_namespace,
        )
        with _insert_module(module_pb2):
            type_defs = xsd.process_xsd(io.StringIO(xsd_str), config)
            if transform_config is not None:
                type_defs = apply_transforms(type_defs, transform_config)
            converter_code = "\n".join(
                xml_converter.generate(py_module, type_defs, module_pb2.__name__),
            )
            module = types.ModuleType(py_module)
            setattr(module, proto_namespace + "_pb2", module_pb2)
            exec(converter_code, module.__dict__)
        return module

    return _factory


@pytest.fixture
def book_xsd() -> str:
    return _BOOK_XSD


@pytest.fixture
def book_type_defs(book_xsd: str) -> tuple[xsd.TypeDefinition, ...]:
    """Provides processed XSD type definitions for the book schema."""
    return xsd.process_xsd(io.StringIO(book_xsd))


@pytest.fixture(name="book_pb2")
def book_pb2_module_fixture(
    pb2_module_factory: Pb2ModuleFactory,
) -> Generator[types.ModuleType, None, None]:
    module, _ = pb2_module_factory(_BOOK_XSD, namespace="book")
    with _insert_module(module):
        yield module


@pytest.fixture(name="book_converter")
def book_converter_module_fixture(
    py_converter_module_factory: PyConverterModuleFactory,
) -> Generator[types.ModuleType, None, None]:
    module = py_converter_module_factory(
        _BOOK_XSD,
        proto_namespace="book",
        py_module="book_converter",
    )
    with _insert_module(module):
        yield module
