# xsdformer

xsdformer converts an XML schema — an **XSD** or a **DTD** — into code that
describes and processes the same data in other ecosystems: Protobuf
definitions, JSON Schema, TypeSpec, Pydantic models, and Python code that
parses conforming XML into Protobuf messages.

It supports enough of XSD/DTD to convert the
[ClinVar](https://www.ncbi.nlm.nih.gov/clinvar/) and
[BioC](http://bioc.sourceforge.net/) schemas. Full coverage of the XSD
specification is a non-goal; the parser raises on constructs it does not model
rather than emitting a silent approximation.

## Pipeline

```text
schema (XSD | DTD) → parse → IR (TypeDefinition graph) → transform → generate → output
```

A front end parses the schema into an intermediate representation (IR). The IR
is a topologically sorted graph of type definitions, independent of both the
input schema language and the output target. Each back end (generator) walks
that IR to emit one output format. Adding an input or output format touches one
front end or one generator, not the pipeline.

## The IR — language

The IR lives in [`xsd/xsd.py`](src/xsdformer/xsd/xsd.py). These are the domain
terms; use them exactly.

**TypeDefinition**:
A named type in the schema. The base for the three concrete kinds a generator
must handle:

- **Message** — a structured type (an XSD complex type): a set of fields.
- **Enumeration** — a closed set of named values.
- **MapType** — a type that models a map (key/value) rather than a record.

**FieldDefinition**:
A member of a `Message`. The base for leaf **Field** kinds and **container**
kinds:

- **Field** — a leaf carrying a value. Subtypes: **Elem** (a child element),
  **Attr** (an XML attribute), **ValueElem** (an element's text content).
- **Seq** — an ordered group of fields.
- **Choice** — a mutually-exclusive group of fields.

**computed_occurs**:
A field's effective `(min, max)` occurrence after multiplying through every
enclosing container. A field that is optional inside a repeated `Seq` is
repeated overall; `computed_occurs` is that resolved multiplicity.

**enclosing_type**:
Links a nested (enclosed) type to the `Message` it is defined inside. A type
with no `enclosing_type` is top-level.

**path**:
The tuple of names from the root schema type down to a given type — its fully
qualified position in the type graph.

## Front ends (parsers)

- [`xsd/xsd.py`](src/xsdformer/xsd/xsd.py) — parses XSD via the `xmlschema`
  library, topologically sorts types by dependency, and produces the IR.
- [`dtd/dtd.py`](src/xsdformer/dtd/dtd.py) — parses DTD into the same IR.
- [`transforms.py`](src/xsdformer/transforms.py) — optional IR-level
  simplifications (dropping fields, inlining single-field wrapper types,
  collapsing a type to a plain string) applied before generation, driven by a
  YAML transform spec (see `clinvar_transforms.yaml`, `pubmed_transforms.yaml`).

## Back ends (generators)

[`generator.py`](src/xsdformer/generator.py) defines the `IGenerator` protocol
and `generate_with()`, which drives any generator over the IR and skips enclosed
types at the top level (the enclosing type emits them). Each generator
implements `IGenerator`:

- [`protobuf/generator.py`](src/xsdformer/protobuf/generator.py) — emits
  `.proto` syntax.
- [`py/xml_converter.py`](src/xsdformer/py/xml_converter.py) — emits Python code
  that converts parsed XML elements into Protobuf message instances. Element
  params are typed against a structural `_Element` Protocol (not `lxml.etree._Element`),
  so the consumer picks the parser — lxml, stdlib `xml.etree.ElementTree`, or
  defusedxml. Runtime helpers (e.g. `_xml_bool`, `_consume`) are embedded into
  the generated output via `inspect.getsource`.
- [`jsonschema/generator.py`](src/xsdformer/jsonschema/generator.py) — compiles
  proto (from the IR or an existing `.proto` file) via `grpc_tools.protoc` into
  a `FileDescriptorSet`, then walks the Protobuf descriptors to emit JSON
  Schema.
- [`typespec/generator.py`](src/xsdformer/typespec/generator.py) — emits
  TypeSpec (`.tsp`). See [ADR 0001](docs/adr/0001-typespec-output-format.md).
- [`pydantic/`](src/xsdformer/pydantic) — emits Pydantic models
  (`generator.py`) and a converter (`converter.py`), sharing naming logic in
  `_naming.py`. See [ADR 0002](docs/adr/0002-pydantic-codegen-and-proto-hub.md).

## CLI

[`tool.py`](src/xsdformer/tool.py) exposes a Click group with three commands:

- `xsdformer xsd <file>` — XSD front end; emit any combination of proto, Python
  converter, JSON Schema, TypeSpec, and Pydantic outputs (one `--*-out` flag
  each).
- `xsdformer dtd <file>` — DTD front end; same output flags.
- `xsdformer proto <file> <namespace>` — Proto-to-JSON-Schema only, bypassing
  the XSD/DTD front end.

## Package builder

[`build.py`](src/xsdformer/build.py) assembles a standalone, pip-installable
Python package from a schema: generated proto (compiled to `_pb2.py`), the XML
converter, and Pydantic models, wrapped in a generated `pyproject.toml`.

The generated package's `protobuf>=` floor is read out of the
`ValidateProtobufRuntimeVersion` call protoc stamps into the `_pb2` (via `ast`,
not a regex), because that call is the assertion the protobuf runtime evaluates
on import — the runtime refuses gencode newer than itself, so any lower floor
makes the package unimportable. The gencode is a property of the protoc bundled
in `grpcio-tools`, and that pin stays the caller's: `grpcio-tools`' own metadata
is not a usable substitute (1.66.2 and 1.71.0 both declare `protobuf>=5.26.1`
while emitting gencode 5.27.2 and 5.29.0).

`build.min_protobuf_runtime` declares the oldest runtime the package is meant to
support. It is checked against the stamped gencode before being emitted, so it
can only raise the floor, never lower it below what the code requires.
`build.dependencies` appends extra requirements; restating `protobuf` or
`pydantic` raises.

## Text utilities

[`xsd/text.py`](src/xsdformer/xsd/text.py) — `snake_case`, `pascal_case`, and
`keep` (wraps a string in `_Exact` to bypass case conversion). All identifier
naming flows through these.
