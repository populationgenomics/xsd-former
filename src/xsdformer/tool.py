import pathlib

import click

from xsdformer.dtd import dtd
from xsdformer.jsonschema import generator as jsonschema_generator
from xsdformer.protobuf import generator
from xsdformer.py import xml_converter
from xsdformer.transforms import TransformConfig, apply_transforms
from xsdformer.xsd import xsd


def _maybe_transform(
    type_defs: tuple[xsd.TypeDefinition, ...],
    transforms: str | None,
) -> tuple[xsd.TypeDefinition, ...]:
    if transforms:
        config = TransformConfig.from_yaml(pathlib.Path(transforms))
        return apply_transforms(type_defs, config)
    return type_defs


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
    main_message: str,
    preserving_proto_field_name: bool,
    proto_package: str,
    transforms: str | None,
) -> None:
    """Converts an XSD file to a Protobuf definition and/or a Python XML converter."""

    type_defs = _maybe_transform(xsd.process_xsd(xsd_file), transforms)

    if not proto_out and not py_out and not json_schema_out:
        namespace = proto_package or pathlib.Path(xsd_file).stem
        for line in generator.generate(namespace, type_defs):
            print(line, flush=True)
        return

    if proto_out:
        namespace = proto_package or pathlib.Path(proto_out).stem
        with open(proto_out, "w") as f:
            for line in generator.generate(namespace, type_defs):
                f.write(line + "\n")

    if py_out:
        if not py_module:
            raise click.UsageError("--py_module is required when using --py_out")
        namespace = pathlib.Path(xsd_file).stem
        with open(py_out, "w") as f:
            for line in xml_converter.generate(namespace, type_defs, py_module):
                f.write(line + "\n")

    if json_schema_out:
        if not main_message:
            raise click.UsageError("--main_message is required when using --json_schema_out")
        namespace = pathlib.Path(json_schema_out).stem
        schema = jsonschema_generator.generate(
            namespace,
            type_defs,
            main_message,
            preserving_proto_field_name=preserving_proto_field_name,
        )
        with open(json_schema_out, "w") as f:
            f.write(schema)


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
        with open(json_schema_out, "w") as f:
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
    main_message: str,
    preserving_proto_field_name: bool,
    proto_package: str,
    transforms: str | None,
) -> None:
    """Converts a DTD file to a Protobuf definition and/or a Python XML converter."""

    type_defs = _maybe_transform(dtd.process_dtd(dtd_file), transforms)

    if not proto_out and not py_out and not json_schema_out:
        namespace = proto_package or pathlib.Path(dtd_file).stem
        for line in generator.generate(namespace, type_defs):
            print(line, flush=True)
        return

    if proto_out:
        namespace = proto_package or pathlib.Path(proto_out).stem
        with open(proto_out, "w") as f:
            for line in generator.generate(namespace, type_defs):
                f.write(line + "\n")

    if py_out:
        if not py_module:
            raise click.UsageError("--py-module is required when using --py-out")
        namespace = pathlib.Path(dtd_file).stem
        with open(py_out, "w") as f:
            for line in xml_converter.generate(namespace, type_defs, py_module):
                f.write(line + "\n")

    if json_schema_out:
        if not main_message:
            raise click.UsageError("--main-message is required when using --json-schema-out")
        namespace = pathlib.Path(json_schema_out).stem
        schema = jsonschema_generator.generate(
            namespace,
            type_defs,
            main_message,
            preserving_proto_field_name=preserving_proto_field_name,
        )
        with open(json_schema_out, "w") as f:
            f.write(schema)


if __name__ == "__main__":
    cli()
