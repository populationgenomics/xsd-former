"""Backbone (pure-Python) tests for the TypeSpec generator — slice 2 (ADR 0001).

Golden/structural assertions on the emitted `.tsp`: a namespace of flat `model`s
with scalar fields and cardinality. No Node toolchain required.
"""

import io

from xsdformer.typespec import generator
from xsdformer.xsd import xsd

# A flat schema: one complexType of scalar fields exercising each cardinality.
_SCALAR_XSD = """
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:complexType name="record">
    <xs:sequence>
      <xs:element name="title" type="xs:string" />
      <xs:element name="comment" type="xs:string" minOccurs="0" />
      <xs:element name="tag" type="xs:string" maxOccurs="unbounded" />
      <xs:element name="count" type="xs:int" />
      <xs:element name="ratio" type="xs:double" />
      <xs:element name="active" type="xs:boolean" />
      <xs:element name="created" type="xs:date" />
    </xs:sequence>
    <xs:attribute name="id" type="xs:ID" use="required" />
    <xs:attribute name="ref" type="xs:string" use="optional" />
  </xs:complexType>
  <xs:element name="record" type="record" />
</xs:schema>
"""


def _generate(xsd_str: str, namespace: str = "demo") -> str:
    type_defs = xsd.process_xsd(io.StringIO(xsd_str))
    return "\n".join(generator.generate(namespace, type_defs))


def test_scalar_model_golden() -> None:
    assert _generate(_SCALAR_XSD) == (
        "namespace Demo;\n"
        "\n"
        "model Record {\n"
        "  id: string;\n"
        "  ref: string?;\n"
        "  title: string;\n"
        "  comment: string?;\n"
        "  tag: string[];\n"
        "  count: int32;\n"
        "  ratio: float64;\n"
        "  active: boolean;\n"
        "  created: utcDateTime;\n"
        "}"
    )


def test_namespace_pascal_cased() -> None:
    out = _generate(_SCALAR_XSD, namespace="my_package")
    assert out.startswith("namespace MyPackage;\n")


def test_dotted_namespace_pascal_cased_per_component() -> None:
    out = _generate(_SCALAR_XSD, namespace="org.my_package.v1")
    assert out.startswith("namespace Org.MyPackage.V1;\n")
