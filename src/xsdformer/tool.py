import dataclasses
import pathlib
from collections.abc import Iterable

import click

from xsdformer.build import build_package
from xsdformer.dtd import dtd
from xsdformer.jsonschema import generator as jsonschema_generator
from xsdformer.protobuf import generator
from xsdformer.py import xml_converter
from xsdformer.pydantic import generator as pydantic_generator
from xsdformer.transforms import BuildConfig, TransformConfig, apply_transforms
from xsdformer.typespec import generator as typespec_generator
from xsdformer.xsd import xsd


def _maybe_transform(
    type_defs: tuple[xsd.TypeDefinition, ...],
    transforms: str | None,
) -> tuple[xsd.TypeDefinition, ...]:
    if transforms:
        config = TransformConfig.from_yaml(pathlib.Path(transforms))
        return apply_transforms(type_defs, config)
    return type_defs


@dataclasses.dataclass(frozen=True)
class _Outputs:
    """The output targets shared by the `xsd` and `dtd` commands."""

    proto_out: str | None
    py_out: str | None
    py_module: str | None
    json_schema_out: str | None
    typespec_out: str | None
    pydantic_out: str | None
    proto_compat: bool
    main_message: str | None
    preserving_proto_field_name: bool
    proto_package: str | None


def _write_lines(path: str, lines: Iterable[str]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


def _emit_stdout(type_defs: tuple[xsd.TypeDefinition, ...], input_path: str, out: _Outputs) -> None:
    namespace = out.proto_package or pathlib.Path(input_path).stem
    for line in generator.generate(namespace, type_defs):
        print(line, flush=True)


def _emit_proto(type_defs: tuple[xsd.TypeDefinition, ...], proto_out: str, out: _Outputs) -> None:
    namespace = out.proto_package or pathlib.Path(proto_out).stem
    _write_lines(proto_out, generator.generate(namespace, type_defs))


def _emit_typespec(type_defs: tuple[xsd.TypeDefinition, ...], typespec_out: str, out: _Outputs) -> None:
    namespace = out.proto_package or pathlib.Path(typespec_out).stem
    _write_lines(typespec_out, typespec_generator.generate(namespace, type_defs, proto_compat=out.proto_compat))


def _emit_pydantic(type_defs: tuple[xsd.TypeDefinition, ...], pydantic_out: str, out: _Outputs) -> None:
    namespace = out.proto_package or pathlib.Path(pydantic_out).stem
    _write_lines(pydantic_out, pydantic_generator.generate(namespace, type_defs))


def _emit_py_converter(type_defs: tuple[xsd.TypeDefinition, ...], py_out: str, input_path: str, out: _Outputs) -> None:
    if not out.py_module:
        raise click.UsageError("--py-module is required when using --py-out")
    namespace = pathlib.Path(input_path).stem
    _write_lines(py_out, xml_converter.generate(namespace, type_defs, out.py_module))


def _emit_json_schema(type_defs: tuple[xsd.TypeDefinition, ...], json_schema_out: str, out: _Outputs) -> None:
    if not out.main_message:
        raise click.UsageError("--main-message is required when using --json-schema-out")
    namespace = pathlib.Path(json_schema_out).stem
    schema = jsonschema_generator.generate(
        namespace,
        type_defs,
        out.main_message,
        preserving_proto_field_name=out.preserving_proto_field_name,
    )
    with open(json_schema_out, "w", encoding="utf-8") as f:
        f.write(schema)


def _emit_outputs(
    type_defs: tuple[xsd.TypeDefinition, ...],
    input_path: str,
    out: _Outputs,
) -> None:
    """Drives the requested generators — the shared body of `xsd` and `dtd`.

    The two commands differ only in their parser (`process_xsd` vs `process_dtd`);
    once they hand over the IR, output emission is identical. Each output target
    is its own emitter so adding a format is a one-line change here.
    """
    if out.proto_compat and not out.typespec_out:
        raise click.UsageError("--proto-compat requires --typespec-out")
    if not any((out.proto_out, out.py_out, out.json_schema_out, out.typespec_out, out.pydantic_out)):
        _emit_stdout(type_defs, input_path, out)
        return
    if out.proto_out:
        _emit_proto(type_defs, out.proto_out, out)
    if out.typespec_out:
        _emit_typespec(type_defs, out.typespec_out, out)
    if out.pydantic_out:
        _emit_pydantic(type_defs, out.pydantic_out, out)
    if out.py_out:
        _emit_py_converter(type_defs, out.py_out, input_path, out)
    if out.json_schema_out:
        _emit_json_schema(type_defs, out.json_schema_out, out)


@click.group()
def cli() -> None:
    """A tool to convert XSD and Protobuf to other formats."""


@cli.command("xsd")
@click.argument("xsd_file", type=click.Path(exists=True))
@click.option("--proto-out", type=click.Path(), help="Output protobuf file.")
@click.option("--py-out", type=click.Path(), help="Output python converter file.")
@click.option(
    "--py-module",
    help="Python protobuf module to import in the converter; required for --py_out.",
    type=str,
)
@click.option("--json-schema-out", type=click.Path(), help="Output JSON schema file.")
@click.option("--typespec-out", type=click.Path(), help="Output TypeSpec (.tsp) file.")
@click.option("--pydantic-out", type=click.Path(), help="Output pydantic models (.py) file.")
@click.option(
    "--proto-compat",
    is_flag=True,
    help="Emit @typespec/protobuf decorations in the .tsp (requires --typespec-out).",
)
@click.option(
    "--main-message",
    help="Main message to use as the root for the JSON schema.",
    type=str,
)
@click.option(
    "--preserving-proto-field-name",
    is_flag=True,
    help="Use the proto field name in the JSON schema, not the json_name.",
)
@click.option(
    "--proto-package",
    help="Package name to use in the protobuf file.",
    type=str,
)
@click.option(
    "--transforms",
    type=click.Path(exists=True),
    help="YAML file specifying IR transforms to apply.",
)
def xsd_command(  # noqa: PLR0913
    xsd_file: str,
    proto_out: str,
    py_out: str,
    py_module: str,
    json_schema_out: str,
    typespec_out: str,
    pydantic_out: str,
    proto_compat: bool,
    main_message: str,
    preserving_proto_field_name: bool,
    proto_package: str,
    transforms: str | None,
) -> None:
    """Converts an XSD file to a Protobuf definition and/or a Python XML converter."""
    type_defs = _maybe_transform(xsd.process_xsd(xsd_file), transforms)
    _emit_outputs(
        type_defs,
        xsd_file,
        _Outputs(
            proto_out=proto_out,
            py_out=py_out,
            py_module=py_module,
            json_schema_out=json_schema_out,
            typespec_out=typespec_out,
            pydantic_out=pydantic_out,
            proto_compat=proto_compat,
            main_message=main_message,
            preserving_proto_field_name=preserving_proto_field_name,
            proto_package=proto_package,
        ),
    )


@cli.command()
@click.argument("proto_file", type=click.Path(exists=True))
@click.argument("namespace", type=str)
@click.option("--main-message", help="Main message to use as the root for the JSON schema.", type=str)
@click.option("--json-schema-out", type=click.Path(), help="Output JSON schema file.")
@click.option(
    "--preserving-proto-field-name",
    is_flag=True,
    help="Use the proto field name in the JSON schema, not the json_name.",
)
@click.option(
    "--include-all",
    is_flag=True,
    help="Include all messages from the proto file, not just those reachable from the main message.",
)
@click.option(
    "--definitions-only",
    is_flag=True,
    help="Generate a schema with only definitions, implies --include-all.",
)
def proto(  # noqa: PLR0913
    proto_file: str,
    namespace: str,
    main_message: str | None,
    json_schema_out: str | None,
    preserving_proto_field_name: bool,
    include_all: bool,
    definitions_only: bool,
) -> None:
    """Converts a .proto file to a JSON schema."""
    if definitions_only:
        include_all = True
    elif not main_message:
        raise click.UsageError("`--main-message` is required unless using `--definitions-only`")

    schema = jsonschema_generator.generate_from_proto(
        pathlib.Path(proto_file),
        namespace,
        main_message,
        preserving_proto_field_name=preserving_proto_field_name,
        include_all=include_all,
        definitions_only=definitions_only,
    )
    if json_schema_out:
        with open(json_schema_out, "w", encoding="utf-8") as f:
            f.write(schema)
    else:
        print(schema, flush=True)


@cli.command("dtd")
@click.argument("dtd_file", type=click.Path(exists=True))
@click.option("--proto-out", type=click.Path(), help="Output protobuf file.")
@click.option("--py-out", type=click.Path(), help="Output python converter file.")
@click.option(
    "--py-module",
    help="Python protobuf module to import in the converter; required for --py-out.",
    type=str,
)
@click.option("--json-schema-out", type=click.Path(), help="Output JSON schema file.")
@click.option("--typespec-out", type=click.Path(), help="Output TypeSpec (.tsp) file.")
@click.option("--pydantic-out", type=click.Path(), help="Output pydantic models (.py) file.")
@click.option(
    "--proto-compat",
    is_flag=True,
    help="Emit @typespec/protobuf decorations in the .tsp (requires --typespec-out).",
)
@click.option(
    "--main-message",
    help="Main message to use as the root for the JSON schema.",
    type=str,
)
@click.option(
    "--preserving-proto-field-name",
    is_flag=True,
    help="Use the proto field name in the JSON schema, not the json_name.",
)
@click.option(
    "--proto-package",
    help="Package name to use in the protobuf file.",
    type=str,
)
@click.option(
    "--transforms",
    type=click.Path(exists=True),
    help="YAML file specifying IR transforms to apply.",
)
def dtd_command(  # noqa: PLR0913
    dtd_file: str,
    proto_out: str,
    py_out: str,
    py_module: str,
    json_schema_out: str,
    typespec_out: str,
    pydantic_out: str,
    proto_compat: bool,
    main_message: str,
    preserving_proto_field_name: bool,
    proto_package: str,
    transforms: str | None,
) -> None:
    """Converts a DTD file to a Protobuf definition and/or a Python XML converter."""
    type_defs = _maybe_transform(dtd.process_dtd(dtd_file), transforms)
    _emit_outputs(
        type_defs,
        dtd_file,
        _Outputs(
            proto_out=proto_out,
            py_out=py_out,
            py_module=py_module,
            json_schema_out=json_schema_out,
            typespec_out=typespec_out,
            pydantic_out=pydantic_out,
            proto_compat=proto_compat,
            main_message=main_message,
            preserving_proto_field_name=preserving_proto_field_name,
            proto_package=proto_package,
        ),
    )


@cli.command("build")
@click.argument("schema_file", type=click.Path(exists=True))
@click.option(
    "--transforms",
    type=click.Path(exists=True),
    help="Transform config YAML (provides build: section too).",
)
@click.option("--namespace", type=str, help="Proto namespace (overrides config).")
@click.option("--package-name", type=str, help="Python package name (overrides config).")
@click.option("--version", type=str, default=None, help="Package version (default: 0.1.0).")
@click.option(
    "--out-dir",
    type=click.Path(),
    default=".",
    show_default=True,
    help="Output directory for generated source tree.",
)
@click.option(
    "--build",
    "run_build",
    is_flag=True,
    help="Also invoke `python -m build --wheel` after source generation.",
)
@click.option(
    "--wheel-out",
    type=click.Path(),
    default=None,
    help="Where to put the .whl file (default: <out-dir>/dist/).",
)
def build_command(  # noqa: PLR0913
    schema_file: str,
    transforms: str | None,
    namespace: str | None,
    package_name: str | None,
    version: str | None,
    out_dir: str,
    run_build: bool,
    wheel_out: str | None,
) -> None:
    """Generates a pip-installable Python package from an XSD or DTD schema."""
    schema_path = pathlib.Path(schema_file)

    # Load build config from transforms YAML if provided.
    build_cfg: BuildConfig | None = None
    if transforms:
        build_cfg = BuildConfig.from_yaml(pathlib.Path(transforms))

    # CLI options override config.
    resolved_namespace = namespace or (build_cfg.namespace if build_cfg else None)
    resolved_package_name = package_name or (build_cfg.package_name if build_cfg else None)
    resolved_version = version or (build_cfg.version if build_cfg else "0.1.0")

    if not resolved_namespace:
        resolved_namespace = schema_path.stem
    if not resolved_package_name:
        raise click.UsageError(
            "--package-name is required (or set build.package_name in the transforms config)",
        )

    # Parse schema.
    suffix = schema_path.suffix.lower()
    type_defs = dtd.process_dtd(str(schema_path)) if suffix == ".dtd" else xsd.process_xsd(str(schema_path))

    if transforms:
        config = TransformConfig.from_yaml(pathlib.Path(transforms))
        type_defs = apply_transforms(type_defs, config)

    package_dir = build_package(
        type_defs=type_defs,
        namespace=resolved_namespace,
        package_name=resolved_package_name,
        version=resolved_version,
        out_dir=pathlib.Path(out_dir),
        run_build=run_build,
        wheel_out=pathlib.Path(wheel_out) if wheel_out else None,
    )
    click.echo(f"Generated package: {package_dir}")


if __name__ == "__main__":
    cli()
