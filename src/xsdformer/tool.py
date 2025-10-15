import pathlib
import sys

import click

from xsdformer.jsonschema import generator as jsonschema_generator
from xsdformer.protobuf import generator
from xsdformer.py import xml_converter
from xsdformer.xsd import xsd


@click.group()
def cli():
    """A tool to convert XSD and Protobuf to other formats."""


@cli.command()
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
def xsd(  # noqa: PLR0913
    xsd_file: str,
    proto_out: str,
    py_out: str,
    py_module: str,
    json_schema_out: str,
    main_message: str,
    preserving_proto_field_name: bool,
    proto_package: str,
) -> None:
    """Converts an XSD file to a Protobuf definition and/or a Python XML converter."""

    type_defs = xsd.process_xsd(xsd_file)

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
@click.argument("main_message", type=str)
@click.option("--json-schema-out", type=click.Path(), help="Output JSON schema file.")
@click.option(
    "--preserving-proto-field-name",
    is_flag=True,
    help="Use the proto field name in the JSON schema, not the json_name.",
)
def proto(
    proto_file: str,
    namespace: str,
    main_message: str,
    json_schema_out: str | None,
    preserving_proto_field_name: bool,
) -> None:
    """Converts a .proto file to a JSON schema."""
    schema = jsonschema_generator.generate_from_proto(
        pathlib.Path(proto_file),
        namespace,
        main_message,
        preserving_proto_field_name=preserving_proto_field_name,
    )
    if json_schema_out:
        with open(json_schema_out, "w") as f:
            f.write(schema)
    else:
        print(schema, flush=True)


if __name__ == "__main__":
    cli()
