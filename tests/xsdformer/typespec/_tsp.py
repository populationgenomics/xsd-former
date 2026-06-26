"""Node/TypeSpec test harness for the gated xsd->tsp->proto round-trip checks.

The TypeSpec toolchain is optional: the round-trip tests are ``skipif``-gated on
:func:`tsp_available`, which is true only when ``node`` is on ``PATH`` and the
``tsp_project`` has its ``node_modules`` installed (``npm install`` in that
directory). See ``docs/adr/0001-typespec-output-format.md``.
"""

import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
from typing import Any

from google.protobuf import descriptor_pb2

TSP_PROJECT_DIR = pathlib.Path(__file__).parent / "tsp_project"
_NODE_MODULES = TSP_PROJECT_DIR / "node_modules"
# Canonical CLI entrypoint shipped by @typespec/compiler.
_TSP_CLI = _NODE_MODULES / "@typespec" / "compiler" / "cmd" / "tsp.js"
_PROTOBUF_EMITTER = _NODE_MODULES / "@typespec" / "protobuf"
_JSON_SCHEMA_EMITTER = _NODE_MODULES / "@typespec" / "json-schema"


class TspCompileError(RuntimeError):
    """Raised when ``tsp compile`` fails (e.g. an unconvertible enum)."""


def tsp_available() -> bool:
    """Whether the TypeSpec toolchain can run the round-trip tests."""
    return shutil.which("node") is not None and _TSP_CLI.is_file() and _PROTOBUF_EMITTER.is_dir()


def json_schema_available() -> bool:
    """Whether the TypeSpec toolchain can run the JSON-Schema equivalence gate."""
    return tsp_available() and _JSON_SCHEMA_EMITTER.is_dir()


def compile_tsp_to_proto(tsp_source: str, work_dir: pathlib.Path) -> str:
    """Compile a ``.tsp`` source string to proto text via ``@typespec/protobuf``.

    ``work_dir`` is an isolated scratch directory (e.g. pytest's ``tmp_path``).
    ``node_modules`` is symlinked in so TypeSpec's node-style import resolution
    finds ``@typespec/protobuf`` from the entrypoint upward, while leaving the
    checked-in project directory untouched.

    Raises:
        TspCompileError: if compilation fails.
    """
    node = shutil.which("node")
    assert node is not None, "node not on PATH (guarded by tsp_available)"
    (work_dir / "node_modules").symlink_to(_NODE_MODULES, target_is_directory=True)
    main_tsp = work_dir / "main.tsp"
    main_tsp.write_text(tsp_source)
    out_dir = work_dir / "out"

    result = subprocess.run(  # noqa: S603
        [
            node,
            str(_TSP_CLI),
            "compile",
            str(main_tsp),
            "--emit",
            "@typespec/protobuf",
            "--output-dir",
            str(out_dir),
        ],
        cwd=work_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise TspCompileError(result.stdout + result.stderr)

    protos = list((out_dir / "@typespec" / "protobuf").glob("*.proto"))
    if len(protos) != 1:
        raise TspCompileError(f"expected exactly one emitted .proto, found {protos!r}")
    return protos[0].read_text()


def compile_tsp_to_json_schema(tsp_source: str, work_dir: pathlib.Path) -> dict[str, Any]:
    """Compile a ``.tsp`` source string to a bundled JSON Schema via ``@typespec/json-schema``.

    Emits one 2020-12 bundle (``$defs`` keyed by type name) with ``emitAllModels``
    so every declared type is present (the emitter is otherwise reachability-based,
    like ``@typespec/protobuf``). ``work_dir`` is an isolated scratch directory;
    ``node_modules`` is symlinked in for TypeSpec's node-style import resolution,
    leaving the checked-in project directory untouched.

    Returns the parsed bundle dict (with relative ``$ref``s as the emitter writes
    them — see ``_equivalence`` for the #4084-style normalization).

    Raises:
        TspCompileError: if compilation fails.
    """
    node = shutil.which("node")
    assert node is not None, "node not on PATH (guarded by json_schema_available)"
    (work_dir / "node_modules").symlink_to(_NODE_MODULES, target_is_directory=True)
    main_tsp = work_dir / "main.tsp"
    main_tsp.write_text(tsp_source)
    out_dir = work_dir / "out"
    bundle_id = "bundle.json"

    result = subprocess.run(  # noqa: S603
        [
            node,
            str(_TSP_CLI),
            "compile",
            str(main_tsp),
            "--emit",
            "@typespec/json-schema",
            "--option",
            "@typespec/json-schema.emitAllModels=true",
            "--option",
            "@typespec/json-schema.file-type=json",
            # int64/uint64 as JSON numbers, not the emitter's default JSON-safe
            # strings — matching the dialect's choice to keep 64-bit ints native
            # Python `int` (ADR 0001 "Scalars"; the converter bridges proto-JSON's
            # string encoding). Without this the contract renders them `string`.
            "--option",
            "@typespec/json-schema.int64-strategy=number",
            "--option",
            f"@typespec/json-schema.bundleId={bundle_id}",
            "--option",
            f"@typespec/json-schema.emitter-output-dir={out_dir}",
        ],
        cwd=work_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise TspCompileError(result.stdout + result.stderr)

    return json.loads((out_dir / bundle_id).read_text())


def proto_to_descriptor_set(
    proto_text: str,
    work_dir: pathlib.Path,
) -> descriptor_pb2.FileDescriptorSet:
    """Compile proto text to a ``FileDescriptorSet`` via ``grpc_tools.protoc``.

    This is the ``proto->descriptor`` half shared with the ``xsd->proto`` path,
    so descriptors from either side are comparable at the wire/semantic level.
    """
    spec = importlib.util.find_spec("google.protobuf.timestamp_pb2")
    assert spec is not None
    assert spec.origin is not None
    proto_include_path = pathlib.Path(spec.origin).parent.parent

    proto_path = work_dir / "from_tsp.proto"
    desc_path = work_dir / "from_tsp.desc"
    proto_path.write_text(proto_text)
    subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            f"--proto_path={work_dir}",
            f"--proto_path={proto_include_path}",
            f"--descriptor_set_out={desc_path}",
            proto_path.name,
        ],
        check=True,
    )
    desc = descriptor_pb2.FileDescriptorSet()
    desc.ParseFromString(desc_path.read_bytes())
    return desc
