import io

from xsdformer.dtd import dtd
from xsdformer.xsd import xsd


def _process(dtd_str: str) -> tuple[xsd.TypeDefinition, ...]:
    return dtd.process_dtd(io.StringIO(dtd_str))


def _by_name(type_defs: tuple[xsd.TypeDefinition, ...]) -> dict[str, xsd.TypeDefinition]:
    return {t.name: t for t in type_defs}


def _fields_by_name(msg: xsd.Message) -> dict[str, xsd.Field]:
    return {f.name: f for f in msg.get_fields()}


def test_simple_sequence() -> None:
    type_defs = _process("""
        <!ELEMENT book (title, author)>
        <!ELEMENT title (#PCDATA)>
        <!ELEMENT author (#PCDATA)>
    """)
    types = _by_name(type_defs)
    assert "Book" in types
    fields = _fields_by_name(types["Book"])
    assert fields["title"].computed_occurs == (1, 1)
    assert fields["author"].computed_occurs == (1, 1)
    assert isinstance(fields["title"].proto_type, xsd.Message)
    assert fields["title"].proto_type.name == "Title"


def test_occurrence_indicators() -> None:
    type_defs = _process("""
        <!ELEMENT root (a, b?, c*, d+)>
        <!ELEMENT a (#PCDATA)>
        <!ELEMENT b (#PCDATA)>
        <!ELEMENT c (#PCDATA)>
        <!ELEMENT d (#PCDATA)>
    """)
    fields = _fields_by_name(_by_name(type_defs)["Root"])
    assert fields["a"].computed_occurs == (1, 1)
    assert fields["b"].computed_occurs == (0, 1)
    assert fields["c"].computed_occurs == (0, None)
    assert fields["d"].computed_occurs == (1, None)


def test_choice() -> None:
    type_defs = _process("""
        <!ELEMENT item (a | b | c)>
        <!ELEMENT a (#PCDATA)>
        <!ELEMENT b (#PCDATA)>
        <!ELEMENT c (#PCDATA)>
    """)
    msg = _by_name(type_defs)["Item"]
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
    fields = _fields_by_name(_by_name(type_defs)["Item"])

    assert isinstance(fields["id"], xsd.Attr)
    assert fields["id"].proto_type is xsd.AtomicType.ID
    assert fields["id"].computed_occurs == (1, 1)

    assert isinstance(fields["name"], xsd.Attr)
    assert fields["name"].proto_type is xsd.AtomicType.STRING
    assert fields["name"].computed_occurs == (0, 1)

    assert isinstance(fields["code"], xsd.Attr)
    assert fields["code"].default == "default_val"
    assert fields["code"].computed_occurs == (0, 1)


def test_enumerated_attribute() -> None:
    type_defs = _process("""
        <!ELEMENT item (#PCDATA)>
        <!ATTLIST item status (draft | published | archived) "draft">
    """)
    types = _by_name(type_defs)
    fields = _fields_by_name(types["Item"])
    attr = fields["status"]
    assert isinstance(attr.proto_type, xsd.Enumeration)
    assert attr.proto_type.enum_values == ("draft", "published", "archived")
    assert attr.default == "draft"


def test_mixed_content_text_only() -> None:
    type_defs = _process("""
        <!ELEMENT para (#PCDATA)>
    """)
    fields = _fields_by_name(_by_name(type_defs)["Para"])
    assert "value" in fields
    assert isinstance(fields["value"], xsd.ValueElem)
    assert fields["value"].proto_type is xsd.AtomicType.STRING


def test_mixed_content_with_elements() -> None:
    type_defs = _process("""
        <!ELEMENT para (#PCDATA | bold | italic)*>
        <!ELEMENT bold (#PCDATA)>
        <!ELEMENT italic (#PCDATA)>
    """)
    msg = _by_name(type_defs)["Para"]
    fields = _fields_by_name(msg)
    assert "value" in fields
    assert fields["value"].proto_type is xsd.AtomicType.STRING
    assert "bold" in fields
    assert fields["bold"].computed_occurs == (0, None)
    assert "italic" in fields
    assert fields["italic"].computed_occurs == (0, None)


def test_empty_element() -> None:
    type_defs = _process("""
        <!ELEMENT br EMPTY>
        <!ATTLIST br clear CDATA #IMPLIED>
    """)
    fields = _fields_by_name(_by_name(type_defs)["Br"])
    assert len(fields) == 1
    assert "clear" in fields
    assert isinstance(fields["clear"], xsd.Attr)


def test_nested_groups() -> None:
    type_defs = _process("""
        <!ELEMENT x ((a, b) | (c, d))>
        <!ELEMENT a (#PCDATA)>
        <!ELEMENT b (#PCDATA)>
        <!ELEMENT c (#PCDATA)>
        <!ELEMENT d (#PCDATA)>
    """)
    msg = _by_name(type_defs)["X"]
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
    list_fields = _fields_by_name(types["List"])
    item_fields = _fields_by_name(types["Item"])
    assert list_fields["item"].proto_type is types["Item"]
    assert item_fields["list"].proto_type is types["List"]


def test_any_element() -> None:
    type_defs = _process("""
        <!ELEMENT container ANY>
    """)
    fields = _fields_by_name(_by_name(type_defs)["Container"])
    assert "value" in fields
    assert isinstance(fields["value"], xsd.ValueElem)
    assert fields["value"].proto_type is xsd.AtomicType.COMPLEXANY


def test_field_numbering() -> None:
    type_defs = _process("""
        <!ELEMENT book (title, author)>
        <!ELEMENT title (#PCDATA)>
        <!ELEMENT author (#PCDATA)>
        <!ATTLIST book id ID #REQUIRED>
    """)
    fields = list(_by_name(type_defs)["Book"].get_fields())
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
    a_flag = _fields_by_name(types["A"])["flag"]
    b_flag = _fields_by_name(types["B"])["flag"]
    assert a_flag.proto_type is b_flag.proto_type


def test_output_ordering_alphabetical() -> None:
    type_defs = _process("""
        <!ELEMENT zebra (#PCDATA)>
        <!ELEMENT alpha (#PCDATA)>
        <!ELEMENT middle (#PCDATA)>
    """)
    names = [t.name for t in type_defs if isinstance(t, xsd.Message)]
    assert names == ["Alpha", "Middle", "Zebra"]
