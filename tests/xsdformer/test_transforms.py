"""Tests for IR transforms."""

import importlib.util
import io
import pathlib
import subprocess
import sys
import types

from lxml import etree

from tests.xsdformer.conftest import PyConverterModuleFactory
from xsdformer.dtd import dtd
from xsdformer.protobuf import generator as proto_gen
from xsdformer.py import xml_converter
from xsdformer.py.xml_converter import _parse_date_element, _serialize_markdown
from xsdformer.transforms import (
    CoercedToTimestampInfo,
    FlattenedListInfo,
    InlinedWrapperInfo,
    MapFieldConfig,
    SerializeContentInfo,
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
        assert isinstance(authors_field.transform_hint, FlattenedListInfo)

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


class TestComments:
    def test_adds_type_comment(self) -> None:
        defs = _parse()
        config = TransformConfig(comments={"Book": "A book record."})
        result = apply_transforms(defs, config)
        book = next(d for d in result if d.name == "Book")
        assert book.documentation == "A book record."

    def test_adds_field_comment(self) -> None:
        defs = _parse()
        config = TransformConfig(comments={"Book.title": "The book title."})
        result = apply_transforms(defs, config)
        book = next(d for d in result if d.name == "Book")
        title_field = next(f for f in book.get_fields() if f.name == "title")
        assert title_field.documentation == "The book title."

    def test_comment_appears_in_proto(self) -> None:
        defs = _parse()
        config = TransformConfig(
            comments={
                "Book": "A book record.",
                "Book.title": "The book title.",
            },
        )
        result = apply_transforms(defs, config)
        proto = "\n".join(proto_gen.generate("test", result))
        assert "// A book record." in proto
        assert "// The book title." in proto


_MIXED_DTD = """
    <!ELEMENT article (title, abstract?)>
    <!ATTLIST article id ID #REQUIRED>
    <!ELEMENT title (#PCDATA | b | i | sup)*>
    <!ATTLIST title lang CDATA #IMPLIED>
    <!ELEMENT abstract (#PCDATA | b | i)*>
    <!ELEMENT b (#PCDATA | b | i | sup)*>
    <!ELEMENT i (#PCDATA | b | i | sup)*>
    <!ELEMENT sup (#PCDATA | b | i)*>
"""


def _parse_dtd(dtd_str: str = _MIXED_DTD) -> tuple[xsd.TypeDefinition, ...]:
    return dtd.process_dtd(io.StringIO(dtd_str))


class TestSerializeContent:
    def test_marks_value_elem_and_drops_elem_fields(self) -> None:
        defs = _parse_dtd()
        config = TransformConfig(serialize_content={"Title": "markdown"})
        result = apply_transforms(defs, config)
        title = next(d for d in result if d.name == "Title")
        fields = list(title.get_fields())
        value_fields = [f for f in fields if isinstance(f, xsd.ValueElem)]
        assert len(value_fields) == 1
        assert isinstance(value_fields[0].transform_hint, SerializeContentInfo)
        assert value_fields[0].transform_hint.serializer == "markdown"
        elem_fields = [f for f in fields if isinstance(f, xsd.Elem)]
        assert all(f.transform_hint is TransformHint.DROPPED for f in elem_fields)

    def test_preserves_attributes(self) -> None:
        defs = _parse_dtd()
        config = TransformConfig(serialize_content={"Title": "markdown"})
        result = apply_transforms(defs, config)
        title = next(d for d in result if d.name == "Title")
        attr_fields = [f for f in title.get_fields() if isinstance(f, xsd.Attr)]
        assert any(f.name == "lang" for f in attr_fields)
        assert all(f.transform_hint is None for f in attr_fields)

    def test_does_not_remove_type_from_defs(self) -> None:
        defs = _parse_dtd()
        config = TransformConfig(serialize_content={"Title": "markdown"})
        result = apply_transforms(defs, config)
        assert any(d.name == "Title" for d in result)

    def test_end_to_end(self, tmp_path: pathlib.Path) -> None:
        type_defs = _parse_dtd()
        config = TransformConfig(serialize_content={"Title": "markdown"})
        type_defs = apply_transforms(type_defs, config)

        namespace = "serialize_test"
        proto_def = "\n".join(proto_gen.generate(namespace, type_defs))
        proto_path = tmp_path / f"{namespace}.proto"
        proto_path.write_text(proto_def)

        spec = importlib.util.find_spec("google.protobuf.timestamp_pb2")
        assert spec is not None
        assert spec.origin is not None
        proto_include = pathlib.Path(spec.origin).parent.parent
        subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-m",
                "grpc_tools.protoc",
                f"--proto_path={tmp_path}",
                f"--proto_path={proto_include}",
                f"--python_out={tmp_path}",
                str(proto_path.relative_to(tmp_path)),
            ],
            check=True,
        )

        pb2_spec = importlib.util.spec_from_file_location(
            f"{namespace}_pb2",
            tmp_path / f"{namespace}_pb2.py",
        )
        assert pb2_spec is not None
        assert pb2_spec.loader is not None
        module_pb2 = importlib.util.module_from_spec(pb2_spec)
        pb2_spec.loader.exec_module(module_pb2)

        converter_code = "\n".join(
            xml_converter.generate(namespace, type_defs, module_pb2.__name__),
        )
        converter = types.ModuleType("serialize_converter")
        old = sys.modules.get(module_pb2.__name__)
        sys.modules[module_pb2.__name__] = module_pb2
        try:
            exec(converter_code, converter.__dict__)
        finally:
            if old is None:
                del sys.modules[module_pb2.__name__]
            else:
                sys.modules[module_pb2.__name__] = old

        # Simple text
        xml1 = b'<article id="a1"><title>Hello world</title></article>'
        result1 = converter.Article(etree.fromstring(xml1))
        assert result1.title.value == "Hello world"

        # With inline markup
        xml2 = b'<article id="a2"><title>Effect of <i>E. coli</i> on growth</title></article>'
        result2 = converter.Article(etree.fromstring(xml2))
        assert result2.title.value == "Effect of *E. coli* on growth"

        # Nested markup
        xml3 = b'<article id="a3"><title><b>Bold <i>and italic</i></b> text</title></article>'
        result3 = converter.Article(etree.fromstring(xml3))
        assert result3.title.value == "**Bold *and italic*** text"

        # Attribute preserved
        assert result1.id == "a1"

        # Superscript
        xml4 = b'<article id="a4"><title>x<sup>2</sup> + y</title></article>'
        result4 = converter.Article(etree.fromstring(xml4))
        assert result4.title.value == "x^(2) + y"

        # Unknown tags: text extracted
        xml5 = b'<article id="a5"><title>See <unknown>content</unknown> here</title></article>'
        result5 = converter.Article(etree.fromstring(xml5))
        assert result5.title.value == "See content here"


class TestSerializeMarkdownDirect:
    """Direct tests of _serialize_markdown without going through the full pipeline."""

    def test_plain_text(self) -> None:
        el = etree.fromstring(b"<t>Hello world</t>")
        assert _serialize_markdown(el) == "Hello world"

    def test_bold(self) -> None:
        el = etree.fromstring(b"<t>Hello <b>world</b></t>")
        assert _serialize_markdown(el) == "Hello **world**"

    def test_italic(self) -> None:
        el = etree.fromstring(b"<t>Hello <i>world</i></t>")
        assert _serialize_markdown(el) == "Hello *world*"

    def test_nested(self) -> None:
        el = etree.fromstring(b"<t><b>Bold <i>and italic</i></b> text</t>")
        assert _serialize_markdown(el) == "**Bold *and italic*** text"

    def test_tail_text(self) -> None:
        el = etree.fromstring(b"<t>a <b>b</b> c <i>d</i> e</t>")
        assert _serialize_markdown(el) == "a **b** c *d* e"

    def test_superscript(self) -> None:
        el = etree.fromstring(b"<t>x<sup>2</sup></t>")
        assert _serialize_markdown(el) == "x^(2)"

    def test_subscript(self) -> None:
        el = etree.fromstring(b"<t>H<sub>2</sub>O</t>")
        assert _serialize_markdown(el) == "H~(2)O"

    def test_unknown_tag_extracts_text(self) -> None:
        el = etree.fromstring(b"<t>See <math>x=1</math> here</t>")
        assert _serialize_markdown(el) == "See x=1 here"


def _build_dtd_converter(
    dtd_str: str,
    config: TransformConfig,
    namespace: str,
    tmp_path: pathlib.Path,
) -> types.ModuleType:
    """Compile a DTD + transforms into a converter module."""
    type_defs = dtd.process_dtd(io.StringIO(dtd_str))
    type_defs = apply_transforms(type_defs, config)

    proto_def = "\n".join(proto_gen.generate(namespace, type_defs))
    proto_path = tmp_path / f"{namespace}.proto"
    proto_path.write_text(proto_def)

    spec = importlib.util.find_spec("google.protobuf.timestamp_pb2")
    assert spec is not None
    assert spec.origin is not None
    proto_include = pathlib.Path(spec.origin).parent.parent
    subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            f"--proto_path={tmp_path}",
            f"--proto_path={proto_include}",
            f"--python_out={tmp_path}",
            str(proto_path.relative_to(tmp_path)),
        ],
        check=True,
    )

    pb2_spec = importlib.util.spec_from_file_location(
        f"{namespace}_pb2",
        tmp_path / f"{namespace}_pb2.py",
    )
    assert pb2_spec is not None
    assert pb2_spec.loader is not None
    module_pb2 = importlib.util.module_from_spec(pb2_spec)
    pb2_spec.loader.exec_module(module_pb2)

    converter_code = "\n".join(
        xml_converter.generate(namespace, type_defs, module_pb2.__name__),
    )
    converter = types.ModuleType(f"{namespace}_converter")
    old = sys.modules.get(module_pb2.__name__)
    sys.modules[module_pb2.__name__] = module_pb2
    try:
        exec(converter_code, converter.__dict__)
    finally:
        if old is None:
            del sys.modules[module_pb2.__name__]
        else:
            sys.modules[module_pb2.__name__] = old
    return converter


_BOOL_DTD = """
    <!ELEMENT root (item+)>
    <!ATTLIST root complete (Y|N) "Y">
    <!ELEMENT item (#PCDATA)>
    <!ATTLIST item active (Y|N) #REQUIRED>
"""


class TestCoerceToBool:
    def test_auto_detects_yn_enums(self) -> None:
        defs = _parse_dtd(_BOOL_DTD)
        config = TransformConfig(coerce_to_bool=True)
        result = apply_transforms(defs, config)
        # The Y/N enum should be removed.
        assert not any(isinstance(d, xsd.Enumeration) for d in result)
        root = next(d for d in result if d.name == "Root")
        complete_field = next(f for f in root.get_fields() if f.name == "complete")
        assert complete_field.proto_type is xsd.AtomicType.BOOL

    def test_end_to_end(self, tmp_path: pathlib.Path) -> None:
        config = TransformConfig(coerce_to_bool=True)
        converter = _build_dtd_converter(_BOOL_DTD, config, "booltest", tmp_path)

        xml = b'<root complete="Y"><item active="Y">a</item><item active="N">b</item></root>'
        result = converter.Root(etree.fromstring(xml))
        assert result.complete is True
        assert result.item[0].active is True
        assert result.item[1].active is False


_DATE_DTD = """
    <!ELEMENT article (title, date_published, date_revised?)>
    <!ELEMENT title (#PCDATA)>
    <!ELEMENT date_published (year, month?, day?)>
    <!ELEMENT date_revised (year, month, day)>
    <!ELEMENT year (#PCDATA)>
    <!ELEMENT month (#PCDATA)>
    <!ELEMENT day (#PCDATA)>
"""


class TestCoerceToTimestamp:
    def test_removes_date_type_and_sets_hint(self) -> None:
        defs = _parse_dtd(_DATE_DTD)
        config = TransformConfig(
            coerce_to_timestamp=frozenset({"DatePublished", "DateRevised"}),
        )
        result = apply_transforms(defs, config)
        names = [d.name for d in result]
        assert "DatePublished" not in names
        assert "DateRevised" not in names
        article = next(d for d in result if d.name == "Article")
        dp_field = next(f for f in article.get_fields() if f.name == "date_published")
        assert dp_field.proto_type is xsd.AtomicType.DATE
        assert isinstance(dp_field.transform_hint, CoercedToTimestampInfo)

    def test_end_to_end_full_date(self, tmp_path: pathlib.Path) -> None:
        config = TransformConfig(
            coerce_to_timestamp=frozenset({"DatePublished", "DateRevised"}),
            inline_wrappers=True,
        )
        converter = _build_dtd_converter(_DATE_DTD, config, "datetest", tmp_path)

        xml = b"""
        <article>
          <title>Test</title>
          <date_published><year>2024</year><month>03</month><day>15</day></date_published>
          <date_revised><year>2024</year><month>06</month><day>01</day></date_revised>
        </article>
        """
        result = converter.Article(etree.fromstring(xml))
        assert result.date_published.seconds != 0
        # 2024-03-15 in UTC
        dt = result.date_published.ToDatetime()
        assert dt.year == 2024
        assert dt.month == 3
        assert dt.day == 15

        dt_rev = result.date_revised.ToDatetime()
        assert dt_rev.year == 2024
        assert dt_rev.month == 6

    def test_end_to_end_partial_date(self, tmp_path: pathlib.Path) -> None:
        config = TransformConfig(
            coerce_to_timestamp=frozenset({"DatePublished"}),
            inline_wrappers=True,
        )
        converter = _build_dtd_converter(_DATE_DTD, config, "datetest2", tmp_path)

        xml = b"""
        <article>
          <title>Test</title>
          <date_published><year>2024</year></date_published>
        </article>
        """
        result = converter.Article(etree.fromstring(xml))
        dt = result.date_published.ToDatetime()
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 1

    def test_end_to_end_month_name(self, tmp_path: pathlib.Path) -> None:
        config = TransformConfig(
            coerce_to_timestamp=frozenset({"DatePublished"}),
            inline_wrappers=True,
        )
        converter = _build_dtd_converter(_DATE_DTD, config, "datetest3", tmp_path)

        xml = b"""
        <article>
          <title>Test</title>
          <date_published><year>2024</year><month>Mar</month></date_published>
        </article>
        """
        result = converter.Article(etree.fromstring(xml))
        dt = result.date_published.ToDatetime()
        assert dt.year == 2024
        assert dt.month == 3

    def test_end_to_end_optional_date(self, tmp_path: pathlib.Path) -> None:
        config = TransformConfig(
            coerce_to_timestamp=frozenset({"DatePublished", "DateRevised"}),
            inline_wrappers=True,
        )
        converter = _build_dtd_converter(_DATE_DTD, config, "datetest4", tmp_path)

        xml = b"""
        <article>
          <title>Test</title>
          <date_published><year>2024</year><month>01</month><day>01</day></date_published>
        </article>
        """
        result = converter.Article(etree.fromstring(xml))
        # date_revised is optional and absent — should be default (empty Timestamp).
        assert result.date_revised.seconds == 0


class TestParseDateElementDirect:
    """Direct tests of _parse_date_element."""

    def test_full_date(self) -> None:
        el = etree.fromstring(b"<d><Year>2024</Year><Month>03</Month><Day>15</Day></d>")
        dt = _parse_date_element(el)
        assert dt.year == 2024
        assert dt.month == 3
        assert dt.day == 15

    def test_year_only(self) -> None:
        el = etree.fromstring(b"<d><Year>2024</Year></d>")
        dt = _parse_date_element(el)
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 1

    def test_month_name(self) -> None:
        el = etree.fromstring(b"<d><Year>2024</Year><Month>Mar</Month></d>")
        dt = _parse_date_element(el)
        assert dt.month == 3

    def test_with_time(self) -> None:
        el = etree.fromstring(
            b"<d><Year>2024</Year><Month>01</Month><Day>15</Day><Hour>10</Hour><Minute>30</Minute></d>",
        )
        dt = _parse_date_element(el)
        assert dt.hour == 10
        assert dt.minute == 30

    def test_medline_date(self) -> None:
        el = etree.fromstring(b"<d><MedlineDate>1998 Dec-1999 Jan</MedlineDate></d>")
        dt = _parse_date_element(el)
        assert dt.year == 1998

    def test_season_ignored(self) -> None:
        el = etree.fromstring(b"<d><Year>2024</Year><Season>Winter</Season></d>")
        dt = _parse_date_element(el)
        assert dt.year == 2024
        assert dt.month == 1


_MAP_DTD = """
    <!ELEMENT catalog (entry*)>
    <!ELEMENT entry EMPTY>
    <!ATTLIST entry
        key   CDATA #REQUIRED
        value CDATA #REQUIRED
    >
"""


class TestMaps:
    def test_removes_message_type(self) -> None:
        defs = _parse_dtd(_MAP_DTD)
        config = TransformConfig(maps={"Entry": MapFieldConfig(key="key", value="value")})
        result = apply_transforms(defs, config)
        assert not any(d.name == "Entry" for d in result)

    def test_rewrites_field_to_map_type(self) -> None:
        defs = _parse_dtd(_MAP_DTD)
        config = TransformConfig(maps={"Entry": MapFieldConfig(key="key", value="value")})
        result = apply_transforms(defs, config)
        catalog = next(d for d in result if d.name == "Catalog")
        entry_field = next(f for f in catalog.get_fields() if f.name == "entry")
        assert isinstance(entry_field.proto_type, xsd.MapType)
        assert entry_field.proto_type.key_type is xsd.AtomicType.STRING
        assert entry_field.proto_type.value_type is xsd.AtomicType.STRING

    def test_end_to_end(self, tmp_path: pathlib.Path) -> None:
        config = TransformConfig(maps={"Entry": MapFieldConfig(key="key", value="value")})
        converter = _build_dtd_converter(_MAP_DTD, config, "maptest", tmp_path)

        xml = b"""
        <catalog>
          <entry key="foo" value="bar"/>
          <entry key="baz" value="qux"/>
        </catalog>
        """
        result = converter.Catalog(etree.fromstring(xml))
        assert result.entry["foo"] == "bar"
        assert result.entry["baz"] == "qux"

    def test_enum_key_raises(self) -> None:
        import pytest
        _ENUM_KEY_DTD = """
            <!ELEMENT catalog (entry*)>
            <!ELEMENT entry EMPTY>
            <!ATTLIST entry
                key   (a|b|c) #REQUIRED
                value CDATA #REQUIRED
            >
        """
        defs = _parse_dtd(_ENUM_KEY_DTD)
        config = TransformConfig(maps={"Entry": MapFieldConfig(key="key", value="value")})
        with pytest.raises(ValueError, match="atomic type"):
            apply_transforms(defs, config)
