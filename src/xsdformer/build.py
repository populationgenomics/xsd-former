"""Build a pip-installable Python package from an XSD/DTD schema."""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys

from xsdformer.protobuf import generator as proto_generator
from xsdformer.py import xml_converter
from xsdformer.pydantic import converter as pydantic_converter
from xsdformer.pydantic import generator as pydantic_generator
from xsdformer.xsd import xsd

_PYPROJECT_TEMPLATE = """\
[project]
name = "{distribution_name}"
version = "{version}"
description = "Generated protobuf package for {namespace}"
requires-python = ">=3.11"
dependencies = ["lxml", "protobuf>=6.32.1", "pydantic>=2"]

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
) -> None:
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


def _write_pyproject(
    namespace: str,
    package_name: str,
    distribution_name: str,
    version: str,
    out_dir: pathlib.Path,
) -> None:
    content = _PYPROJECT_TEMPLATE.format(
        package_name=package_name,
        distribution_name=distribution_name,
        namespace=namespace,
        version=version,
    )
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
) -> pathlib.Path:
    """Generates a pip-installable source tree (and optionally builds a wheel).

    `package_name` names the importable module directory; `distribution_name` is
    the PyPI/distribution name (`[project] name`), defaulting to `package_name`.

    Returns the package directory path.
    """
    if distribution_name is None:
        distribution_name = package_name
    if out_dir is None:
        out_dir = pathlib.Path('.')
    out_dir = out_dir.resolve()
    package_dir = out_dir / package_name
    package_dir.mkdir(parents=True, exist_ok=True)

    # Generate and compile the proto.
    _write_proto(namespace, type_defs, package_dir)
    _compile_proto(namespace, package_dir, out_dir)

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
    _write_pyproject(namespace, package_name, distribution_name, version, out_dir)

    if run_build:
        build_cmd = [sys.executable, '-m', 'build', '--wheel']
        if wheel_out:
            build_cmd.extend(['--outdir', str(wheel_out)])
        subprocess.run(build_cmd, check=True, cwd=out_dir)  # noqa: S603

    return package_dir
