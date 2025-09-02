import pathlib

import click

from xsdformer.protobuf import generator
from xsdformer.py import xml_converter
from xsdformer.xsd import xsd


@click.command()
@click.argument("xsd_file", type=click.Path(exists=True))
@click.option("--proto_out", type=click.Path(), help="Output protobuf file.")
@click.option("--py_out", type=click.Path(), help="Output python converter file.")
@click.option(
  "--py_module",
  help="Python protobuf module to import in the converter; required for --py_out.",
  type=str,
)
def main(
  xsd_file: str,
  proto_out: str,
  py_out: str,
  py_module: str,
) -> None:
  """Converts an XSD file to a Protobuf definition and/or a Python XML converter."""

  type_defs = xsd.process_xsd(xsd_file)

  if not proto_out and not py_out:
    namespace = pathlib.Path(xsd_file).stem
    for line in generator.generate(f"{namespace}.proto", type_defs):
      print(line, flush=True)
    return

  if proto_out:
    namespace = pathlib.Path(proto_out).stem
    with open(proto_out, "w") as f:
      for line in generator.generate(f"{namespace}.proto", type_defs):
        f.write(line + "\n")

  if py_out:
    if not py_module:
      raise click.UsageError("--py_module is required when using --py_out")
    namespace = pathlib.Path(xsd_file).stem
    with open(py_out, "w") as f:
      for line in xml_converter.generate(namespace, type_defs, py_module):
        f.write(line + "\n")


if __name__ == "__main__":
  main()
