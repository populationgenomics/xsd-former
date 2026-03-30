"""Tests for IR transforms."""

import io

from lxml import etree

from tests.xsdformer.conftest import PyConverterModuleFactory
from xsdformer.transforms import (
    InlinedWrapperInfo,
    TransformConfig,
    TransformHint,
    apply_transforms,
)
from xsdformer.xsd import xsd

_WRAPPER_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:complexType name="nameWrapper">
    <xs:sequence>
      <xs:element name="value" type="xs:string" />
    </xs:sequence>
  </xs:complexType>

  <xs:complexType name="authorList">
    <xs:sequence>
      <xs:element name="author" type="xs:string" maxOccurs="unbounded" />
    </xs:sequence>
  </xs:complexType>

  <xs:complexType name="book">
    <xs:sequence>
      <xs:element name="name" type="nameWrapper" />
      <xs:element name="authors" type="authorList" />
      <xs:element name="title" type="xs:string" />
      <xs:element name="comment" type="xs:string" minOccurs="0" />
    </xs:sequence>
  </xs:complexType>

  <xs:element name="book" type="book" />
</xs:schema>
"""


def _parse(xsd_str: str = _WRAPPER_XSD) -> tuple[xsd.TypeDefinition, ...]:
    return xsd.process_xsd(io.StringIO(xsd_str))


def _names(defs: tuple[xsd.TypeDefinition, ...]) -> list[str | None]:
    return [d.name for d in defs]


class TestNoOpTransform:
    def test_passthrough(self) -> None:
        defs = _parse()
        config = TransformConfig()
        result = apply_transforms(defs, config)
        assert _names(result) == _names(defs)


class TestInlineWrappers:
    def test_removes_single_atomic_field_wrapper(self) -> None:
        defs = _parse()
        config = TransformConfig(inline_wrappers=True)
        result = apply_transforms(defs, config)
        names = _names(result)
        assert "NameWrapper" not in names
        # AuthorList has a repeated field, so it should NOT be inlined.
        assert "AuthorList" in names

    def test_rewrites_referencing_field_to_atomic(self) -> None:
        defs = _parse()
        config = TransformConfig(inline_wrappers=True)
        result = apply_transforms(defs, config)
        book = next(d for d in result if d.name == "Book")
        name_field = next(f for f in book.get_fields() if f.name == "name")
        assert isinstance(name_field.proto_type, xsd.AtomicType)
        assert name_field.proto_type == xsd.AtomicType.STRING
        assert isinstance(name_field.transform_hint, InlinedWrapperInfo)

    def test_end_to_end(self, py_converter_module_factory: PyConverterModuleFactory) -> None:
        config = TransformConfig(inline_wrappers=True)
        module = py_converter_module_factory(
            _WRAPPER_XSD,
            transform_config=config,
            proto_namespace="wrapper",
            py_module="wrapper_converter",
        )
        xml = b"""
        <book>
          <name><value>The Name</value></name>
          <authors><author>Alice</author></authors>
          <title>A Title</title>
        </book>
        """
        result = module.Book(etree.fromstring(xml))
        assert result.name == "The Name"


class TestFlattenListWrappers:
    def test_removes_single_repeated_field_wrapper(self) -> None:
        defs = _parse()
        config = TransformConfig(flatten_list_wrappers=True)
        result = apply_transforms(defs, config)
        names = _names(result)
        assert "AuthorList" not in names
        # NameWrapper has a non-repeated field, so it should NOT be flattened.
        assert "NameWrapper" in names

    def test_rewrites_field_to_inner_type_repeated(self) -> None:
        defs = _parse()
        config = TransformConfig(flatten_list_wrappers=True)
        result = apply_transforms(defs, config)
        book = next(d for d in result if d.name == "Book")
        authors_field = next(f for f in book.get_fields() if f.name == "authors")
        assert authors_field.is_repeated
        assert authors_field.proto_type == xsd.AtomicType.STRING
        assert authors_field.transform_hint is TransformHint.FLATTENED_LIST

    def test_end_to_end(self, py_converter_module_factory: PyConverterModuleFactory) -> None:
        config = TransformConfig(flatten_list_wrappers=True)
        module = py_converter_module_factory(
            _WRAPPER_XSD,
            transform_config=config,
            proto_namespace="flat",
            py_module="flat_converter",
        )
        xml = b"""
        <book>
          <name><value>The Name</value></name>
          <authors>
            <author>Alice</author>
            <author>Bob</author>
          </authors>
          <title>A Title</title>
        </book>
        """
        result = module.Book(etree.fromstring(xml))
        assert list(result.authors) == ["Alice", "Bob"]


class TestDropTypes:
    def test_removes_type_and_marks_field_dropped(self) -> None:
        defs = _parse()
        config = TransformConfig(drop_types=frozenset({"NameWrapper"}))
        result = apply_transforms(defs, config)
        names = _names(result)
        assert "NameWrapper" not in names
        book = next(d for d in result if d.name == "Book")
        name_field = next(f for f in book.get_fields() if f.name == "name")
        assert name_field.transform_hint is TransformHint.DROPPED

    def test_end_to_end(self, py_converter_module_factory: PyConverterModuleFactory) -> None:
        config = TransformConfig(drop_types=frozenset({"NameWrapper"}))
        module = py_converter_module_factory(
            _WRAPPER_XSD,
            transform_config=config,
            proto_namespace="drop",
            py_module="drop_converter",
        )
        xml = b"""
        <book>
          <name><value>ignored</value></name>
          <authors><author>Alice</author></authors>
          <title>A Title</title>
        </book>
        """
        result = module.Book(etree.fromstring(xml))
        assert not hasattr(result, "name") or result.name == ""
        assert result.title == "A Title"


class TestDropFields:
    def test_marks_field_dropped(self) -> None:
        defs = _parse()
        config = TransformConfig(
            drop_fields={"Book": frozenset({"comment"})},
        )
        result = apply_transforms(defs, config)
        book = next(d for d in result if d.name == "Book")
        comment_field = next(f for f in book.get_fields() if f.name == "comment")
        assert comment_field.transform_hint is TransformHint.DROPPED

    def test_end_to_end(self, py_converter_module_factory: PyConverterModuleFactory) -> None:
        config = TransformConfig(
            drop_fields={"Book": frozenset({"comment"})},
        )
        module = py_converter_module_factory(
            _WRAPPER_XSD,
            transform_config=config,
            proto_namespace="dropf",
            py_module="dropf_converter",
        )
        xml = b"""
        <book>
          <name><value>The Name</value></name>
          <authors><author>Alice</author></authors>
          <title>A Title</title>
          <comment>A comment</comment>
        </book>
        """
        result = module.Book(etree.fromstring(xml))
        assert result.title == "A Title"
        # comment was dropped from the proto, so the field doesn't exist.
        assert not hasattr(result, "comment")


class TestCollapseToString:
    def test_removes_type_and_marks_collapsed(self) -> None:
        defs = _parse()
        config = TransformConfig(collapse_to_string=frozenset({"NameWrapper"}))
        result = apply_transforms(defs, config)
        names = _names(result)
        assert "NameWrapper" not in names
        book = next(d for d in result if d.name == "Book")
        name_field = next(f for f in book.get_fields() if f.name == "name")
        assert name_field.proto_type is xsd.AtomicType.COMPLEXANY
        assert name_field.transform_hint is TransformHint.COLLAPSED_TO_STRING

    def test_end_to_end(self, py_converter_module_factory: PyConverterModuleFactory) -> None:
        config = TransformConfig(
            collapse_to_string=frozenset({"NameWrapper"}),
        )
        module = py_converter_module_factory(
            _WRAPPER_XSD,
            transform_config=config,
            proto_namespace="collapse",
            py_module="collapse_converter",
        )
        xml = b"""
        <book>
          <name><value>The Name</value></name>
          <authors><author>Alice</author></authors>
          <title>A Title</title>
        </book>
        """
        result = module.Book(etree.fromstring(xml))
        # Collapsed to string, so name contains the serialized XML.
        assert "<value>" in result.name


class TestRenameTypes:
    def test_renames_type(self) -> None:
        defs = _parse()
        config = TransformConfig(
            rename_types={"NameWrapper": "Name"},
        )
        result = apply_transforms(defs, config)
        names = _names(result)
        assert "Name" in names
        assert "NameWrapper" not in names


class TestRenameFields:
    def test_renames_field(self) -> None:
        defs = _parse()
        config = TransformConfig(
            rename_fields={"Book": {"title": "book_title"}},
        )
        result = apply_transforms(defs, config)
        book = next(d for d in result if d.name == "Book")
        field_names = [f.name for f in book.get_fields()]
        assert "book_title" in field_names
        assert "title" not in field_names


class TestRenumber:
    def test_dropped_fields_get_no_number(self) -> None:
        defs = _parse()
        config = TransformConfig(
            drop_fields={"Book": frozenset({"comment"})},
        )
        result = apply_transforms(defs, config)
        book = next(d for d in result if d.name == "Book")
        fields = list(book.get_fields())
        non_dropped = [f for f in fields if f.transform_hint is not TransformHint.DROPPED]
        # Field numbers should be sequential starting from 1.
        assert [f.num for f in non_dropped] == list(range(1, len(non_dropped) + 1))
        # Dropped field has no number.
        dropped = [f for f in fields if f.transform_hint is TransformHint.DROPPED]
        assert all(f.num is None for f in dropped)


class TestCombined:
    def test_inline_and_flatten(self) -> None:
        defs = _parse()
        config = TransformConfig(
            inline_wrappers=True,
            flatten_list_wrappers=True,
        )
        result = apply_transforms(defs, config)
        names = _names(result)
        assert "NameWrapper" not in names
        assert "AuthorList" not in names
        assert "Book" in names
