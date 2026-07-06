"""Tests for the build_package function."""

from __future__ import annotations

import io
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from xsdformer.build import build_package
from xsdformer.xsd import xsd

if TYPE_CHECKING:
    import pathlib

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
    </xs:sequence>
    <xs:attribute name="id" type="xs:ID" use="required" />
    <xs:attribute name="status" type="status" use="optional" />
  </xs:complexType>

  <xs:simpleType name="role">
    <xs:restriction base="xs:string">
      <xs:enumeration value="author" />
      <xs:enumeration value="editor" />
    </xs:restriction>
  </xs:simpleType>

  <xs:simpleType name="status">
    <xs:restriction base="xs:string">
      <xs:enumeration value="new" />
      <xs:enumeration value="used" />
    </xs:restriction>
  </xs:simpleType>

  <xs:element name="book" type="book" />
</xs:schema>
"""


@pytest.fixture
def book_xsd_file(tmp_path: pathlib.Path) -> pathlib.Path:
    xsd_file = tmp_path / 'book.xsd'
    xsd_file.write_text(_BOOK_XSD)
    return xsd_file


@pytest.fixture
def built_package(
    tmp_path: pathlib.Path,
    book_xsd_file: pathlib.Path,  # noqa: ARG001
) -> tuple[pathlib.Path, pathlib.Path]:
    type_defs = xsd.process_xsd(io.StringIO(_BOOK_XSD))
    out_dir = tmp_path / 'out'
    out_dir.mkdir()
    package_dir = build_package(
        type_defs=type_defs,
        namespace='book',
        package_name='book_proto',
        version='1.2.3',
        out_dir=out_dir,
    )
    return out_dir, package_dir


def test_build_returns_package_dir(built_package: tuple[pathlib.Path, pathlib.Path]) -> None:
    out_dir, package_dir = built_package
    assert package_dir == out_dir / 'book_proto'


def test_generated_file_tree(built_package: tuple[pathlib.Path, pathlib.Path]) -> None:
    _, package_dir = built_package
    expected_files = {
        '__init__.py',
        'book.proto',
        'book_pb2.py',
        'book_pb2.pyi',
        'xml_converter.py',
        'models.py',
        'pydantic_converter.py',
        'py.typed',
    }
    actual_files = {f.name for f in package_dir.iterdir() if f.is_file()}
    assert expected_files == actual_files


def test_pyproject_toml(built_package: tuple[pathlib.Path, pathlib.Path]) -> None:
    out_dir, _ = built_package
    pyproject = (out_dir / 'pyproject.toml').read_text()
    assert 'name = "book_proto"' in pyproject
    assert 'version = "1.2.3"' in pyproject
    assert '"book_proto/book.proto" = "book_proto/book.proto"' in pyproject
    assert 'hatchling' in pyproject
    assert 'pydantic>=2' in pyproject
    assert 'defusedxml' not in pyproject


def test_pyproject_distribution_name(tmp_path: pathlib.Path) -> None:
    """distribution_name sets [project] name; package_name still names the module dir."""
    type_defs = xsd.process_xsd(io.StringIO(_BOOK_XSD))
    out_dir = tmp_path / 'out'
    out_dir.mkdir()
    package_dir = build_package(
        type_defs=type_defs,
        namespace='book',
        package_name='pubmed_proto',
        distribution_name='pubmed-proto',
        version='1.1.0',
        out_dir=out_dir,
    )
    pyproject = (out_dir / 'pyproject.toml').read_text()
    assert 'name = "pubmed-proto"' in pyproject  # PyPI/distribution name
    assert 'packages = ["pubmed_proto"]' in pyproject  # importable module dir
    assert '"pubmed_proto/book.proto" = "pubmed_proto/book.proto"' in pyproject
    assert package_dir == out_dir / 'pubmed_proto'


def test_init_py(built_package: tuple[pathlib.Path, pathlib.Path]) -> None:
    _, package_dir = built_package
    init = (package_dir / '__init__.py').read_text()
    assert 'book_pb2' in init
    assert 'xml_converter' in init
    assert 'models' in init
    assert 'pydantic_converter' in init


def test_proto_file_content(built_package: tuple[pathlib.Path, pathlib.Path]) -> None:
    _, package_dir = built_package
    proto = (package_dir / 'book.proto').read_text()
    assert 'syntax' in proto
    assert 'Book' in proto


def test_xml_converter_importable(built_package: tuple[pathlib.Path, pathlib.Path]) -> None:
    out_dir, _ = built_package
    script = f"""
import sys
sys.path.insert(0, {str(out_dir)!r})
import book_proto.xml_converter
"""
    result = subprocess.run(  # noqa: S603
        [sys.executable, '-c', script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_pb2_importable(built_package: tuple[pathlib.Path, pathlib.Path]) -> None:
    out_dir, _ = built_package
    script = f"""
import sys
sys.path.insert(0, {str(out_dir)!r})
import book_proto.book_pb2
"""
    result = subprocess.run(  # noqa: S603
        [sys.executable, '-c', script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_package_importable(built_package: tuple[pathlib.Path, pathlib.Path]) -> None:
    # Importing the package re-exports models + pydantic_converter, which in turn
    # imports the compiled *_pb2 and models — exercising the whole generated suite.
    out_dir, _ = built_package
    script = f"""
import sys
sys.path.insert(0, {str(out_dir)!r})
import book_proto
book_proto.models
book_proto.pydantic_converter
book_proto.pydantic_converter.Book_from_proto
book_proto.pydantic_converter.Book_to_proto
"""
    result = subprocess.run(  # noqa: S603
        [sys.executable, '-c', script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_proto_pydantic_roundtrip(built_package: tuple[pathlib.Path, pathlib.Path]) -> None:
    # A live proto -> pydantic -> proto round-trip over the generated suite.
    out_dir, _ = built_package
    script = f"""
import sys
sys.path.insert(0, {str(out_dir)!r})
from book_proto import book_pb2, pydantic_converter

proto = book_pb2.Book(id="b1", title="T", isbn="123")
proto.authors.author.add().name = "Ann"
model = pydantic_converter.Book_from_proto(proto)
assert model.id == "b1"
assert model.title == "T"
assert model.authors.author[0].name == "Ann"
assert pydantic_converter.Book_to_proto(model) == proto
"""
    result = subprocess.run(  # noqa: S603
        [sys.executable, '-c', script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
