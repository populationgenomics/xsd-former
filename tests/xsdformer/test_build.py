"""Tests for the build_package function."""

from __future__ import annotations

import io
import subprocess
import sys
import tomllib
from typing import TYPE_CHECKING

import pytest

from xsdformer import build, transforms
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
    package_dir = build.build_package(
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


def test_pyproject_protobuf_floor_is_the_stamped_gencode(
    built_package: tuple[pathlib.Path, pathlib.Path],
) -> None:
    """The declared protobuf floor equals the gencode protoc stamped into the _pb2.

    Both sides come from the same build, so this holds under any grpcio-tools
    version rather than pinning an expected value that would drift.
    """
    out_dir, package_dir = built_package
    major, minor, patch = build._gencode_version(package_dir / 'book_pb2.py')
    data = tomllib.loads((out_dir / 'pyproject.toml').read_text())
    assert f'protobuf>={major}.{minor}.{patch}' in data['project']['dependencies']


def test_pyproject_minimal_omits_optional_metadata(built_package: tuple[pathlib.Path, pathlib.Path]) -> None:
    """With no metadata configured, the optional [project] fields are absent."""
    out_dir, _ = built_package
    data = tomllib.loads((out_dir / 'pyproject.toml').read_text())
    project = data['project']
    assert 'license' not in project
    assert 'classifiers' not in project
    assert 'authors' not in project
    assert 'keywords' not in project
    assert 'urls' not in project


def test_pyproject_metadata(tmp_path: pathlib.Path) -> None:
    """Configured [project] metadata is emitted, and the result is valid TOML."""
    type_defs = xsd.process_xsd(io.StringIO(_BOOK_XSD))
    out_dir = tmp_path / 'out'
    out_dir.mkdir()
    build.build_package(
        type_defs=type_defs,
        namespace='book',
        package_name='book_proto',
        version='1.0.0',
        out_dir=out_dir,
        description='Book protobufs',
        license_expr='MIT',
        keywords=['book', 'proto'],
        classifiers=['Programming Language :: Python :: 3', 'Typing :: Typed'],
        authors=[
            transforms.Author(name='Centre for Population Genomics'),
            transforms.Author(name='A', email='a@b.org'),
        ],
        urls=[('Repository', 'https://github.com/populationgenomics/example')],
    )
    data = tomllib.loads((out_dir / 'pyproject.toml').read_text())  # must parse as TOML
    project = data['project']
    assert project['description'] == 'Book protobufs'
    assert project['license'] == 'MIT'
    assert project['keywords'] == ['book', 'proto']
    assert 'Typing :: Typed' in project['classifiers']
    assert {'name': 'Centre for Population Genomics'} in project['authors']
    assert {'name': 'A', 'email': 'a@b.org'} in project['authors']
    assert data['project']['urls']['Repository'] == 'https://github.com/populationgenomics/example'


def test_pyproject_readme_and_license_file(tmp_path: pathlib.Path) -> None:
    """readme/license_file are copied into the build root and referenced in pyproject."""
    readme = tmp_path / 'README.md'
    readme.write_text('# Doc\n')
    license_path = tmp_path / 'LICENSE'
    license_path.write_text('MIT license text\n')
    type_defs = xsd.process_xsd(io.StringIO(_BOOK_XSD))
    out_dir = tmp_path / 'out'
    out_dir.mkdir()
    build.build_package(
        type_defs=type_defs,
        namespace='book',
        package_name='book_proto',
        version='1.0.0',
        out_dir=out_dir,
        license_expr='MIT',
        readme=readme,
        license_file=license_path,
    )
    data = tomllib.loads((out_dir / 'pyproject.toml').read_text())
    assert data['project']['readme'] == 'README.md'
    assert data['project']['license-files'] == ['LICENSE']
    assert (out_dir / 'README.md').read_text() == '# Doc\n'
    assert (out_dir / 'LICENSE').read_text() == 'MIT license text\n'


def test_pyproject_distribution_name(tmp_path: pathlib.Path) -> None:
    """distribution_name sets [project] name; package_name still names the module dir."""
    type_defs = xsd.process_xsd(io.StringIO(_BOOK_XSD))
    out_dir = tmp_path / 'out'
    out_dir.mkdir()
    package_dir = build.build_package(
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


_VALIDATE_POSITIONAL = """\
from google.protobuf import runtime_version as _runtime_version
_runtime_version.ValidateProtobufRuntimeVersion(
    _runtime_version.Domain.PUBLIC,
    6,
    31,
    1,
    '',
    'pkg/t.proto'
)
"""

_VALIDATE_KEYWORD = """\
from google.protobuf import runtime_version as _runtime_version
_runtime_version.ValidateProtobufRuntimeVersion(
    gen_domain=_runtime_version.Domain.PUBLIC,
    gen_major=6, gen_minor=31, gen_patch=1,
    gen_suffix='', location='pkg/t.proto'
)
"""

_VALIDATE_BARE_NAME = """\
from google.protobuf.runtime_version import Domain, ValidateProtobufRuntimeVersion
ValidateProtobufRuntimeVersion(Domain.PUBLIC, 5, 27, 2, '', 'pkg/t.proto')
"""


class TestGencodeVersion:
    """Reading the stamped gencode version out of a generated _pb2."""

    @pytest.mark.parametrize(
        ('source', 'expected'),
        [
            (_VALIDATE_POSITIONAL, (6, 31, 1)),
            (_VALIDATE_KEYWORD, (6, 31, 1)),
            (_VALIDATE_BARE_NAME, (5, 27, 2)),
        ],
        ids=['positional', 'keyword', 'bare-name'],
    )
    def test_reads_version(
        self,
        tmp_path: pathlib.Path,
        source: str,
        expected: tuple[int, int, int],
    ) -> None:
        pb2 = tmp_path / 'x_pb2.py'
        pb2.write_text(source)
        assert build._gencode_version(pb2) == expected

    def test_raises_when_absent(self, tmp_path: pathlib.Path) -> None:
        """A protoc older than the protobuf 5.27 line emits no assertion to read."""
        pb2 = tmp_path / 'x_pb2.py'
        pb2.write_text('DESCRIPTOR = None\n')
        with pytest.raises(RuntimeError, match='found 0'):
            build._gencode_version(pb2)

    def test_raises_when_ambiguous(self, tmp_path: pathlib.Path) -> None:
        pb2 = tmp_path / 'x_pb2.py'
        pb2.write_text(_VALIDATE_POSITIONAL + _VALIDATE_POSITIONAL)
        with pytest.raises(RuntimeError, match='found 2'):
            build._gencode_version(pb2)

    def test_raises_on_unreadable_argument(self, tmp_path: pathlib.Path) -> None:
        pb2 = tmp_path / 'x_pb2.py'
        pb2.write_text(_VALIDATE_POSITIONAL.replace('    31,', '    _MINOR,'))
        with pytest.raises(RuntimeError, match='gen_minor'):
            build._gencode_version(pb2)
