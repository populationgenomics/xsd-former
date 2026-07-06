import importlib.util
import io
import pathlib
import subprocess
import sys
import types

from lxml import etree

from xsdformer.dtd import dtd
from xsdformer.protobuf import generator as proto_gen
from xsdformer.py import xml_converter
from xsdformer.xsd import xsd


def _process(dtd_str: str) -> tuple[xsd.TypeDefinition, ...]:
    return dtd.process_dtd(io.StringIO(dtd_str))


def _by_name(type_defs: tuple[xsd.TypeDefinition, ...]) -> dict[str, xsd.Message]:
    return {t.name: t for t in type_defs if isinstance(t, xsd.Message)}


def _fields_by_name(msg: xsd.Message) -> dict[str, xsd.Field]:
    return {f.name: f for f in msg.get_fields()}


def test_simple_sequence() -> None:
    type_defs = _process("""
        <!ELEMENT book (title, author)>
        <!ELEMENT title (#PCDATA)>
        <!ELEMENT author (#PCDATA)>
    """)
    types = _by_name(type_defs)
    assert 'Book' in types
    fields = _fields_by_name(types['Book'])
    assert fields['title'].computed_occurs == (1, 1)
    assert fields['author'].computed_occurs == (1, 1)
    assert isinstance(fields['title'].proto_type, xsd.Message)
    assert fields['title'].proto_type.name == 'Title'


def test_occurrence_indicators() -> None:
    type_defs = _process("""
        <!ELEMENT root (a, b?, c*, d+)>
        <!ELEMENT a (#PCDATA)>
        <!ELEMENT b (#PCDATA)>
        <!ELEMENT c (#PCDATA)>
        <!ELEMENT d (#PCDATA)>
    """)
    fields = _fields_by_name(_by_name(type_defs)['Root'])
    assert fields['a'].computed_occurs == (1, 1)
    assert fields['b'].computed_occurs == (0, 1)
    assert fields['c'].computed_occurs == (0, None)
    assert fields['d'].computed_occurs == (1, None)


def test_choice() -> None:
    type_defs = _process("""
        <!ELEMENT item (a | b | c)>
        <!ELEMENT a (#PCDATA)>
        <!ELEMENT b (#PCDATA)>
        <!ELEMENT c (#PCDATA)>
    """)
    msg = _by_name(type_defs)['Item']
    # Choice at top level: content should contain a Choice container.
    assert len(msg.content) == 1
    assert isinstance(msg.content[0], xsd.Choice)
    assert len(msg.content[0].content) == 3


def test_attributes_cdata_and_id() -> None:
    type_defs = _process("""
        <!ELEMENT item (#PCDATA)>
        <!ATTLIST item
            id ID #REQUIRED
            name CDATA #IMPLIED
            code CDATA "default_val">
    """)
    fields = _fields_by_name(_by_name(type_defs)['Item'])

    assert isinstance(fields['id'], xsd.Attr)
    assert fields['id'].proto_type is xsd.AtomicType.ID
    assert fields['id'].computed_occurs == (1, 1)

    assert isinstance(fields['name'], xsd.Attr)
    assert fields['name'].proto_type is xsd.AtomicType.STRING
    assert fields['name'].computed_occurs == (0, 1)

    assert isinstance(fields['code'], xsd.Attr)
    assert fields['code'].default == 'default_val'
    assert fields['code'].computed_occurs == (0, 1)


def test_enumerated_attribute() -> None:
    type_defs = _process("""
        <!ELEMENT item (#PCDATA)>
        <!ATTLIST item status (draft | published | archived) "draft">
    """)
    types = _by_name(type_defs)
    fields = _fields_by_name(types['Item'])
    attr = fields['status']
    assert isinstance(attr, xsd.Attr)
    assert isinstance(attr.proto_type, xsd.Enumeration)
    assert attr.proto_type.enum_values == ('draft', 'published', 'archived')
    assert attr.default == 'draft'


def test_mixed_content_text_only() -> None:
    type_defs = _process("""
        <!ELEMENT para (#PCDATA)>
    """)
    fields = _fields_by_name(_by_name(type_defs)['Para'])
    assert 'value' in fields
    assert isinstance(fields['value'], xsd.ValueElem)
    assert fields['value'].proto_type is xsd.AtomicType.STRING


def test_mixed_content_with_elements() -> None:
    type_defs = _process("""
        <!ELEMENT para (#PCDATA | bold | italic)*>
        <!ELEMENT bold (#PCDATA)>
        <!ELEMENT italic (#PCDATA)>
    """)
    msg = _by_name(type_defs)['Para']
    fields = _fields_by_name(msg)
    assert 'value' in fields
    assert fields['value'].proto_type is xsd.AtomicType.STRING
    assert 'bold' in fields
    assert fields['bold'].computed_occurs == (0, None)
    assert 'italic' in fields
    assert fields['italic'].computed_occurs == (0, None)


def test_empty_element() -> None:
    type_defs = _process("""
        <!ELEMENT br EMPTY>
        <!ATTLIST br clear CDATA #IMPLIED>
    """)
    fields = _fields_by_name(_by_name(type_defs)['Br'])
    assert len(fields) == 1
    assert 'clear' in fields
    assert isinstance(fields['clear'], xsd.Attr)


def test_nested_groups() -> None:
    type_defs = _process("""
        <!ELEMENT x ((a, b) | (c, d))>
        <!ELEMENT a (#PCDATA)>
        <!ELEMENT b (#PCDATA)>
        <!ELEMENT c (#PCDATA)>
        <!ELEMENT d (#PCDATA)>
    """)
    msg = _by_name(type_defs)['X']
    # Should have a Choice with two Seq children.
    assert len(msg.content) == 1
    choice = msg.content[0]
    assert isinstance(choice, xsd.Choice)
    assert len(choice.content) == 2
    assert isinstance(choice.content[0], xsd.Seq)
    assert isinstance(choice.content[1], xsd.Seq)


def test_circular_references() -> None:
    type_defs = _process("""
        <!ELEMENT list (item+)>
        <!ELEMENT item (text, list?)>
        <!ELEMENT text (#PCDATA)>
    """)
    types = _by_name(type_defs)
    list_fields = _fields_by_name(types['List'])
    item_fields = _fields_by_name(types['Item'])
    assert list_fields['item'].proto_type is types['Item']
    assert item_fields['list'].proto_type is types['List']


def test_any_element() -> None:
    type_defs = _process("""
        <!ELEMENT container ANY>
    """)
    fields = _fields_by_name(_by_name(type_defs)['Container'])
    assert 'value' in fields
    assert isinstance(fields['value'], xsd.ValueElem)
    assert fields['value'].proto_type is xsd.AtomicType.COMPLEXANY


def test_field_numbering() -> None:
    type_defs = _process("""
        <!ELEMENT book (title, author)>
        <!ELEMENT title (#PCDATA)>
        <!ELEMENT author (#PCDATA)>
        <!ATTLIST book id ID #REQUIRED>
    """)
    fields = list(_by_name(type_defs)['Book'].get_fields())
    nums = [f.num for f in fields]
    assert nums == [1, 2, 3]


def test_shared_enum() -> None:
    """Two attributes with the same enum values should share the Enumeration."""
    type_defs = _process("""
        <!ELEMENT a (#PCDATA)>
        <!ATTLIST a flag (yes | no) #REQUIRED>
        <!ELEMENT b (#PCDATA)>
        <!ATTLIST b flag (yes | no) #REQUIRED>
    """)
    types = _by_name(type_defs)
    a_flag = _fields_by_name(types['A'])['flag']
    b_flag = _fields_by_name(types['B'])['flag']
    assert a_flag.proto_type is b_flag.proto_type


def test_duplicate_fields_in_choice() -> None:
    """Same element in multiple choice branches gets deduplicated for proto numbering."""
    type_defs = _process("""
        <!ELEMENT article ((pagination, elocation_id*) | elocation_id+)>
        <!ELEMENT pagination (#PCDATA)>
        <!ELEMENT elocation_id (#PCDATA)>
    """)
    msg = _by_name(type_defs)['Article']
    # Both elocation_id fields should have the same field number.
    all_fields = list(msg.get_fields())
    eid_fields = [f for f in all_fields if f.name == 'elocation_id']
    assert len(eid_fields) == 2
    assert eid_fields[0].num == eid_fields[1].num


def test_duplicate_fields_proto_generation() -> None:
    """Proto generation emits duplicate field name only once."""
    type_defs = _process("""
        <!ELEMENT article ((pagination, elocation_id*) | elocation_id+)>
        <!ELEMENT pagination (#PCDATA)>
        <!ELEMENT elocation_id (#PCDATA)>
    """)
    proto_lines = list(proto_gen.generate('test', type_defs))
    # elocation_id should appear exactly once as a field definition.
    field_lines = [line.strip() for line in proto_lines if 'elocation_id' in line and '=' in line]
    assert len(field_lines) == 1
    assert 'repeated' in field_lines[0]


def test_undefined_element_stub() -> None:
    """Elements referenced in content but not declared get stub Messages."""
    type_defs = _process("""
        <!ELEMENT container (known, unknown_elem)>
        <!ELEMENT known (#PCDATA)>
    """)
    types = _by_name(type_defs)
    assert 'UnknownElem' in types
    stub_fields = _fields_by_name(types['UnknownElem'])
    assert 'value' in stub_fields
    assert stub_fields['value'].proto_type is xsd.AtomicType.COMPLEXANY


def test_converter_with_choice_duplicates(tmp_path: pathlib.Path) -> None:
    """XML converter correctly handles choice branches with overlapping elements."""
    dtd_str = """
        <!ELEMENT root ((pagination, eid*) | eid+)>
        <!ELEMENT pagination (#PCDATA)>
        <!ELEMENT eid (#PCDATA)>
    """

    type_defs = _process(dtd_str)
    namespace = 'testdup'
    proto_def = '\n'.join(proto_gen.generate(namespace, type_defs))
    proto_path = tmp_path / f'{namespace}.proto'
    proto_path.write_text(proto_def)

    # Compile proto
    spec = importlib.util.find_spec('google.protobuf.timestamp_pb2')
    proto_include = pathlib.Path(spec.origin).parent.parent
    subprocess.run(  # noqa: S603
        [
            sys.executable,
            '-m',
            'grpc_tools.protoc',
            f'--proto_path={tmp_path}',
            f'--proto_path={proto_include}',
            f'--python_out={tmp_path}',
            str(proto_path.relative_to(tmp_path)),
        ],
        check=True,
    )

    # Load compiled module
    pb2_spec = importlib.util.spec_from_file_location(
        f'{namespace}_pb2',
        tmp_path / f'{namespace}_pb2.py',
    )
    module_pb2 = importlib.util.module_from_spec(pb2_spec)
    pb2_spec.loader.exec_module(module_pb2)

    # Generate and load converter
    converter_code = '\n'.join(xml_converter.generate(namespace, type_defs, module_pb2.__name__))
    converter = types.ModuleType('testdup_converter')
    old = sys.modules.get(module_pb2.__name__)
    sys.modules[module_pb2.__name__] = module_pb2
    try:
        exec(converter_code, converter.__dict__)
    finally:
        if old is None:
            del sys.modules[module_pb2.__name__]
        else:
            sys.modules[module_pb2.__name__] = old

    # Test branch 1: pagination + eid
    xml1 = b'<root><pagination>p1</pagination><eid>e1</eid><eid>e2</eid></root>'
    proto1 = converter.Root(etree.fromstring(xml1))
    assert proto1.pagination.value == 'p1'
    assert len(proto1.eid) == 2

    # Test branch 2: eid only
    xml2 = b'<root><eid>e1</eid></root>'
    proto2 = converter.Root(etree.fromstring(xml2))
    assert len(proto2.eid) == 1


def test_output_ordering_alphabetical() -> None:
    type_defs = _process("""
        <!ELEMENT zebra (#PCDATA)>
        <!ELEMENT alpha (#PCDATA)>
        <!ELEMENT middle (#PCDATA)>
    """)
    names = [t.name for t in type_defs if isinstance(t, xsd.Message)]
    assert names == ['Alpha', 'Middle', 'Zebra']
