"""Build a pip-installable Python package from an XSD/DTD schema."""

from __future__ import annotations

import ast
import importlib.metadata
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
from collections.abc import Sequence

from xsdformer import transforms
from xsdformer.protobuf import generator as proto_generator
from xsdformer.py import xml_converter
from xsdformer.pydantic import converter as pydantic_converter
from xsdformer.pydantic import generator as pydantic_generator
from xsdformer.xsd import xsd

# The static tail of the generated pyproject.toml. The [project] table is
# assembled separately (see _write_pyproject) because its optional fields and
# TOML arrays/tables don't format cleanly from one static string.
_BUILD_SYSTEM_TEMPLATE = """\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["{package_name}"]

[tool.hatch.build.targets.wheel.force-include]
"{package_name}/{namespace}.proto" = "{package_name}/{namespace}.proto"
"""

_INIT_TEMPLATE = """\
from {package_name} import {namespace}_pb2 as {namespace}_pb2
from {package_name} import models as models
from {package_name} import pydantic_converter as pydantic_converter
from {package_name} import xml_converter as xml_converter
"""


def _proto_include_path() -> pathlib.Path:
    spec = importlib.util.find_spec('google.protobuf.timestamp_pb2')
    if spec is None or spec.origin is None:
        raise RuntimeError('Could not find google.protobuf package')
    return pathlib.Path(spec.origin).parent.parent


def _write_proto(
    namespace: str,
    type_defs: tuple[xsd.TypeDefinition, ...],
    package_dir: pathlib.Path,
) -> pathlib.Path:
    proto_path = package_dir / f'{namespace}.proto'
    with open(proto_path, 'w') as f:
        for line in proto_generator.generate(namespace, type_defs):
            f.write(line + '\n')
    return proto_path


def _compile_proto(
    namespace: str,
    package_dir: pathlib.Path,
    out_dir: pathlib.Path,
) -> pathlib.Path:
    """Compiles the emitted `.proto`, returning the path of the generated `_pb2.py`."""
    proto_include = _proto_include_path()
    subprocess.run(  # noqa: S603
        [
            sys.executable,
            '-m',
            'grpc_tools.protoc',
            f'--proto_path={out_dir}',
            f'--proto_path={proto_include}',
            f'--python_out={out_dir}',
            f'--pyi_out={out_dir}',
            f'{package_dir.name}/{namespace}.proto',
        ],
        check=True,
    )
    return package_dir / f'{namespace}_pb2.py'


_GENCODE_VALIDATOR = 'ValidateProtobufRuntimeVersion'

# Argument name and positional index of each version component in
# ValidateProtobufRuntimeVersion(gen_domain, gen_major, gen_minor, gen_patch,
# gen_suffix, location). protoc emits them positionally; keywords are read as
# well so the extraction survives a change of emission style.
_GENCODE_ARGS = (('gen_major', 1), ('gen_minor', 2), ('gen_patch', 3))


def _called_name(call: ast.Call) -> str | None:
    match call.func:
        case ast.Attribute(attr=name) | ast.Name(id=name):
            return name
        case _:
            return None


def _gencode_version(pb2_path: pathlib.Path) -> tuple[int, int, int]:
    """Reads the protobuf gencode version protoc stamped into a generated `_pb2.py`.

    Taken from the `ValidateProtobufRuntimeVersion` call rather than the
    `# Protobuf Python Version:` header comment: that call is the assertion the
    protobuf runtime evaluates on import, so it is the constraint itself rather
    than a description of it.

    Args:
        pb2_path: Path to a `_pb2.py` emitted by protoc.

    Returns:
        The gencode `(major, minor, patch)`.

    Raises:
        RuntimeError: If the file does not contain exactly one such call (protoc
            older than the protobuf 5.27 line does not emit it at all), or passes
            its version arguments in an unrecognised shape.
    """
    tree = ast.parse(pb2_path.read_text(), filename=str(pb2_path))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _called_name(n) == _GENCODE_VALIDATOR]
    if len(calls) != 1:
        raise RuntimeError(
            f'Expected exactly one {_GENCODE_VALIDATOR} call in {pb2_path}, found {len(calls)}. '
            'The protobuf gencode version cannot be determined, so the generated '
            "package's protobuf requirement cannot be derived.",
        )
    call = calls[0]
    keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
    version: list[int] = []
    for name, index in _GENCODE_ARGS:
        node = keywords.get(name)
        if node is None and index < len(call.args):
            node = call.args[index]
        if not isinstance(node, ast.Constant) or not isinstance(node.value, int):
            raise RuntimeError(f'Could not read {name} from {_GENCODE_VALIDATOR} in {pb2_path}.')
        version.append(node.value)
    major, minor, patch = version
    return major, minor, patch


def _toolchain_version() -> str:
    """The resolved grpcio-tools version, for diagnostics only."""
    try:
        return importlib.metadata.version('grpcio-tools')
    except importlib.metadata.PackageNotFoundError:
        # Only enriches an error message; a missing distribution must not mask
        # the error being reported.
        return '(unknown version)'


def _parse_version(value: str, source: str) -> tuple[int, int, int]:
    """Parses a one-to-three component dotted version, zero-filling what is absent."""
    parts = value.split('.')
    if not 1 <= len(parts) <= 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f'{source} must be one to three dot-separated integers, got {value!r}.')
    padded = (*(int(part) for part in parts), 0, 0)
    return padded[0], padded[1], padded[2]


def _resolve_protobuf_floor(
    gencode_version: tuple[int, int, int],
    min_protobuf_runtime: str | None,
) -> str:
    """Resolves the protobuf floor to declare for the generated package.

    Args:
        gencode_version: Gencode version stamped into the generated `_pb2`.
        min_protobuf_runtime: Oldest protobuf runtime the caller promises the
            generated package supports, or None to take the gencode as the floor.

    Returns:
        The version to declare as the generated package's `protobuf>=` floor.

    Raises:
        RuntimeError: If `min_protobuf_runtime` is older than the stamped gencode,
            which the protobuf runtime would refuse to load.
        ValueError: If `min_protobuf_runtime` is not a dotted version.
    """
    gencode = '.'.join(str(part) for part in gencode_version)
    if min_protobuf_runtime is None:
        return gencode
    if gencode_version > _parse_version(min_protobuf_runtime, 'min_protobuf_runtime'):
        raise RuntimeError(
            f'The protoc bundled in grpcio-tools {_toolchain_version()} stamped gencode {gencode} '
            f'into the generated _pb2, but min_protobuf_runtime promises support for protobuf '
            f'{min_protobuf_runtime}. The protobuf runtime refuses gencode newer than itself, so '
            f'the generated package would not import at {min_protobuf_runtime}. Either pin '
            f'grpcio-tools to a release whose protoc emits gencode {min_protobuf_runtime} or '
            f'older, or raise min_protobuf_runtime to {gencode}.',
        )
    return min_protobuf_runtime


def _write_converter(
    namespace: str,
    type_defs: tuple[xsd.TypeDefinition, ...],
    package_name: str,
    package_dir: pathlib.Path,
) -> None:
    module = f'{package_name}.{namespace}_pb2'
    with open(package_dir / 'xml_converter.py', 'w') as f:
        for line in xml_converter.generate(namespace, type_defs, module):
            f.write(line + '\n')


def _write_pydantic_models(
    namespace: str,
    type_defs: tuple[xsd.TypeDefinition, ...],
    package_dir: pathlib.Path,
) -> None:
    with open(package_dir / 'models.py', 'w') as f:
        for line in pydantic_generator.generate(namespace, type_defs):
            f.write(line + '\n')


def _write_pydantic_converter(
    namespace: str,
    type_defs: tuple[xsd.TypeDefinition, ...],
    package_name: str,
    package_dir: pathlib.Path,
) -> None:
    proto_module = f'{package_name}.{namespace}_pb2'
    models_module = f'{package_name}.models'
    with open(package_dir / 'pydantic_converter.py', 'w') as f:
        for line in pydantic_converter.generate(namespace, type_defs, proto_module, models_module):
            f.write(line + '\n')


def _toml_str(value: str) -> str:
    r"""A TOML basic string for `value`.

    JSON string syntax is a subset of TOML basic-string syntax (same escapes,
    `\uXXXX` for control/non-ASCII), so `json.dumps` yields a correctly-quoted
    TOML string for any value — avoiding hand-rolled quoting bugs.
    """
    return json.dumps(value)


def _write_pyproject(
    namespace: str,
    package_name: str,
    distribution_name: str,
    version: str,
    out_dir: pathlib.Path,
    protobuf_floor: str,
    *,
    description: str | None = None,
    readme: str | None = None,
    license_expr: str | None = None,
    license_files: Sequence[str] = (),
    keywords: Sequence[str] = (),
    classifiers: Sequence[str] = (),
    authors: Sequence[transforms.Author] = (),
    urls: Sequence[tuple[str, str]] = (),
) -> None:
    project = [
        '[project]',
        f'name = {_toml_str(distribution_name)}',
        f'version = {_toml_str(version)}',
        f'description = {_toml_str(description or f"Generated protobuf package for {namespace}")}',
        'requires-python = ">=3.11"',
    ]
    if readme:
        project.append(f'readme = {_toml_str(readme)}')
    if license_expr:
        project.append(f'license = {_toml_str(license_expr)}')
    if license_files:
        project.append(f'license-files = [{", ".join(_toml_str(f) for f in license_files)}]')
    if keywords:
        project.append(f'keywords = [{", ".join(_toml_str(k) for k in keywords)}]')
    if authors:
        project.append('authors = [')
        for author in authors:
            fields = f'name = {_toml_str(author.name)}'
            if author.email:
                fields += f', email = {_toml_str(author.email)}'
            project.append(f'  {{ {fields} }},')
        project.append(']')
    if classifiers:
        project.append('classifiers = [')
        project.extend(f'  {_toml_str(c)},' for c in classifiers)
        project.append(']')
    project.append(f'dependencies = ["protobuf>={protobuf_floor}", "pydantic>=2"]')
    if urls:
        project.append('')
        project.append('[project.urls]')
        project.extend(f'{_toml_str(label)} = {_toml_str(url)}' for label, url in urls)

    build_system = _BUILD_SYSTEM_TEMPLATE.format(package_name=package_name, namespace=namespace)
    content = '\n'.join(project) + '\n\n' + build_system
    (out_dir / 'pyproject.toml').write_text(content)


def build_package(
    type_defs: tuple[xsd.TypeDefinition, ...],
    namespace: str,
    package_name: str,
    distribution_name: str | None = None,
    version: str = '0.1.0',
    out_dir: pathlib.Path | None = None,
    run_build: bool = False,
    wheel_out: pathlib.Path | None = None,
    *,
    description: str | None = None,
    readme: pathlib.Path | None = None,
    license_expr: str | None = None,
    license_file: pathlib.Path | None = None,
    keywords: Sequence[str] = (),
    classifiers: Sequence[str] = (),
    authors: Sequence[transforms.Author] = (),
    urls: Sequence[tuple[str, str]] = (),
    min_protobuf_runtime: str | None = None,
) -> pathlib.Path:
    """Generates a pip-installable source tree (and optionally builds a wheel).

    `package_name` names the importable module directory; `distribution_name` is
    the PyPI/distribution name (`[project] name`), defaulting to `package_name`.
    The remaining keyword arguments are optional `[project]` metadata; each is
    emitted only when set, so the default output stays minimal. `readme` and
    `license_file`, when given, are copied into the build root and referenced by
    `readme` / `license-files`.

    `min_protobuf_runtime` is the oldest protobuf runtime the caller promises the
    generated package supports. When given it becomes the declared `protobuf>=`
    floor, and the build fails if the toolchain stamps gencode newer than it.
    When unset, the floor is the stamped gencode.

    Returns the package directory path.
    """
    if distribution_name is None:
        distribution_name = package_name
    if out_dir is None:
        out_dir = pathlib.Path('.')
    out_dir = out_dir.resolve()
    package_dir = out_dir / package_name
    package_dir.mkdir(parents=True, exist_ok=True)

    # Generate and compile the proto. The gencode version protoc stamps into the
    # _pb2 bounds the generated package's protobuf floor from below: the runtime
    # refuses gencode newer than itself, so a lower floor is unimportable.
    _write_proto(namespace, type_defs, package_dir)
    pb2_path = _compile_proto(namespace, package_dir, out_dir)
    protobuf_floor = _resolve_protobuf_floor(_gencode_version(pb2_path), min_protobuf_runtime)

    # Generate the XML converter.
    _write_converter(namespace, type_defs, package_name, package_dir)

    # Generate the pydantic models and the proto <-> pydantic converter.
    _write_pydantic_models(namespace, type_defs, package_dir)
    _write_pydantic_converter(namespace, type_defs, package_name, package_dir)

    # Write package metadata files.
    (package_dir / '__init__.py').write_text(
        _INIT_TEMPLATE.format(package_name=package_name, namespace=namespace),
    )
    (package_dir / 'py.typed').write_text('')

    # Copy readme/license into the build root so hatchling can resolve them.
    readme_name = None
    if readme is not None:
        readme_name = readme.name
        shutil.copyfile(readme, out_dir / readme_name)
    license_files: tuple[str, ...] = ()
    if license_file is not None:
        shutil.copyfile(license_file, out_dir / license_file.name)
        license_files = (license_file.name,)

    _write_pyproject(
        namespace,
        package_name,
        distribution_name,
        version,
        out_dir,
        protobuf_floor,
        description=description,
        readme=readme_name,
        license_expr=license_expr,
        license_files=license_files,
        keywords=keywords,
        classifiers=classifiers,
        authors=authors,
        urls=urls,
    )

    if run_build:
        build_cmd = [sys.executable, '-m', 'build', '--wheel']
        if wheel_out:
            build_cmd.extend(['--outdir', str(wheel_out)])
        subprocess.run(build_cmd, check=True, cwd=out_dir)  # noqa: S603

    return package_dir
