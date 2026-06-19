"""Slice 1 spike: stand up the gated tsp->proto round-trip harness.

These tests resolve the open risk in ADR 0001: whether ``@typespec/protobuf``
accepts string-valued enum members (``SOME_NAME: "value"``) and auto-numbers
them. It does not — it requires explicit integer values with a zero first
member — so ``--proto-compat`` (slice 6) must emit integer-valued enums. The
default, non-proto-compat ``.tsp`` is free to keep readable string values.
"""

import io
import pathlib

import pytest
from google.protobuf import descriptor_pb2

from tests.xsdformer.conftest import _BOOK_XSD
from tests.xsdformer.typespec import _tsp
from xsdformer.protobuf import generator as proto_generator
from xsdformer.typespec import generator as typespec_generator
from xsdformer.xsd import text, xsd

pytestmark = pytest.mark.skipif(
    not _tsp.tsp_available(),
    reason="TypeSpec toolchain unavailable (run `npm install` in tests/xsdformer/typespec/tsp_project)",
)

# proto-compat enum form: proto value names as members, explicit integers, zero first.
_INTEGER_ENUM_TSP = """\
import "@typespec/protobuf";
using Protobuf;

@package({name: "spike"})
namespace Spike;

enum Role {
  ROLE_UNSPECIFIED: 0,
  ROLE_AUTHOR: 1,
  ROLE_EDITOR: 2,
  ROLE_REVIEWER: 3,
}

model Person {
  @field(1) role: Role;
}
"""

# The form ADR 0001 asked about: string-valued members, no explicit numbers.
_STRING_ENUM_TSP = """\
import "@typespec/protobuf";
using Protobuf;

@package({name: "spike"})
namespace Spike;

enum Role {
  ROLE_UNSPECIFIED: "",
  ROLE_AUTHOR: "author",
  ROLE_EDITOR: "editor",
  ROLE_REVIEWER: "reviewer",
}

model Person {
  @field(1) role: Role;
}
"""


def test_integer_enum_round_trips_to_proto(tmp_path: pathlib.Path) -> None:
    """Integer-valued enums survive tsp->proto with member names and numbers intact."""
    proto_text = _tsp.compile_tsp_to_proto(_INTEGER_ENUM_TSP, tmp_path)
    desc = _tsp.proto_to_descriptor_set(proto_text, tmp_path)

    (file_desc,) = desc.file
    assert file_desc.package == "spike"
    (enum_desc,) = file_desc.enum_type
    assert enum_desc.name == "Role"
    assert [(v.name, v.number) for v in enum_desc.value] == [
        ("ROLE_UNSPECIFIED", 0),
        ("ROLE_AUTHOR", 1),
        ("ROLE_EDITOR", 2),
        ("ROLE_REVIEWER", 3),
    ]


def test_string_valued_enum_is_rejected(tmp_path: pathlib.Path) -> None:
    """@typespec/protobuf rejects string-valued enums: proto-compat needs integers."""
    with pytest.raises(_tsp.TspCompileError) as exc_info:
        _tsp.compile_tsp_to_proto(_STRING_ENUM_TSP, tmp_path)
    assert "unconvertible-enum" in str(exc_info.value)


_PACKAGE = "book"


def _norm_type_name(type_name: str) -> str:
    """Canonicalizes a descriptor `type_name` for cross-path comparison.

    Drops the leading `.package.` and joins the remaining components with `_`, so
    a proto nested reference (`.book.Parent.Child`) and the tsp hoisted name
    (`.book.Parent_Child`) compare equal — the nested-vs-hoisted placement that
    ADR 0001 deems cosmetic.
    """
    name = type_name.removeprefix(f".{_PACKAGE}.")
    return name.replace(".", "_")


def _normalize(desc: descriptor_pb2.FileDescriptorSet) -> dict:
    """Reduces a descriptor set to its wire/semantic essentials (ADR 0001).

    Captures field numbers, types, repeated-ness, and enum numbers — flattening
    nested messages to top-level `Parent_Child` keys — while discarding package
    name, declaration order, `oneof` grouping, and proto3 optional markers, all
    of which the governing invariant tolerates.
    """
    messages: dict[str, dict] = {}
    enums: dict[str, list] = {}

    def walk_enum(enum_desc: descriptor_pb2.EnumDescriptorProto, path: tuple[str, ...]) -> None:
        # Canonicalize each member to its bare value suffix (`GERMLINE`), dropping
        # the enum-name prefix that both sides carry but in different forms: tsp
        # hoists enums to the namespace and prefixes members with the *full* path
        # (`SAMPLE_ORIGIN_GERMLINE`) to dodge proto's package-scoped enum-value
        # collisions, while the protobuf generator nests the enum and keeps only
        # the *local* prefix (`ORIGIN_GERMLINE`). Splitting the joined key on `_`
        # recovers the path components either way (`pascal_case` strips
        # underscores, so every `_` is a path delimiter); the full prefix is all
        # components screamed, the local prefix just the last. Try the full prefix
        # first so a local name that contains its parent (e.g. `Issn`/`IssnType`)
        # isn't mis-stripped.
        key = "_".join(path)
        components = key.split("_")
        full_prefix = "_".join(text.snake_case(c).upper() for c in components) + "_"
        local_prefix = text.snake_case(components[-1]).upper() + "_"

        def suffix(name: str) -> str:
            if name.startswith(full_prefix):
                return name[len(full_prefix) :]
            return name.removeprefix(local_prefix)

        enums[key] = sorted((suffix(v.name), v.number) for v in enum_desc.value)

    def walk_message(msg_desc: descriptor_pb2.DescriptorProto, path: tuple[str, ...]) -> None:
        key = "_".join(path)
        messages[key] = {
            f.name: (f.number, f.type, _norm_type_name(f.type_name), f.label == f.LABEL_REPEATED)
            for f in msg_desc.field
        }
        for nested in msg_desc.nested_type:
            walk_message(nested, (*path, nested.name))
        for nested_enum in msg_desc.enum_type:
            walk_enum(nested_enum, (*path, nested_enum.name))

    (file_desc,) = desc.file
    for msg in file_desc.message_type:
        walk_message(msg, (msg.name,))
    for enum in file_desc.enum_type:
        walk_enum(enum, (enum.name,))

    # `@typespec/protobuf` is reachability-based: it emits only enums referenced
    # by a field. The protobuf generator emits every declared type, including dead
    # enums (e.g. clinvar's `ChromosomeStr`). A type referenced by nothing carries
    # no data, so it is outside the wire/semantic invariant — restrict the enum
    # comparison to enums some field actually references.
    referenced = {field[2] for fields in messages.values() for field in fields.values()}
    enums = {k: v for k, v in enums.items() if k in referenced}
    return {"messages": messages, "enums": enums}


def test_book_xsd_proto_round_trips_via_tsp(tmp_path: pathlib.Path) -> None:
    """`xsd->proto` ≡ `xsd->tsp->proto` at the wire/semantic level (ADR 0001)."""
    type_defs = xsd.process_xsd(io.StringIO(_BOOK_XSD))

    tsp_dir, direct_dir, roundtrip_dir = (tmp_path / "tsp", tmp_path / "direct", tmp_path / "roundtrip")
    for d in (tsp_dir, direct_dir, roundtrip_dir):
        d.mkdir()

    direct_proto = "\n".join(proto_generator.generate(_PACKAGE, type_defs))
    tsp_source = "\n".join(typespec_generator.generate(_PACKAGE, type_defs, proto_compat=True))
    via_tsp_proto = _tsp.compile_tsp_to_proto(tsp_source, tsp_dir)

    direct_desc = _tsp.proto_to_descriptor_set(direct_proto, direct_dir)
    via_tsp_desc = _tsp.proto_to_descriptor_set(via_tsp_proto, roundtrip_dir)

    assert _normalize(direct_desc) == _normalize(via_tsp_desc)
