"""Slice 1 spike: stand up the gated tsp->proto round-trip harness.

These tests resolve the open risk in ADR 0001: whether ``@typespec/protobuf``
accepts string-valued enum members (``SOME_NAME: "value"``) and auto-numbers
them. It does not — it requires explicit integer values with a zero first
member — so ``--proto-compat`` (slice 6) must emit integer-valued enums. The
default, non-proto-compat ``.tsp`` is free to keep readable string values.
"""

import pathlib

import pytest

from tests.xsdformer.typespec import _tsp

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
