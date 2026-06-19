# XSDFormer

XSDFormer deforms [XML schema definitions
(XSDs)](https://www.w3.org/XML/Schema) into [Protobuf
definitions](https://protobuf.dev/). It also generates Python code
to convert parsed XML into the corresponding Protobuf representation.

It supports enough of the XSD specification to convert the
[ClinVar](https://www.ncbi.nlm.nih.gov/clinvar/) and
[BioC](http://bioc.sourceforge.net/) schemas; full support for all
XSD features is a non-goal.

## Why convert from XSD to Protobuf?

While XML and XSDs (or alternatively JSON and JSON schemas) are
powerful for defining complex, human-readable data structures, they
have some drawbacks, especially in high-performance applications
or when dealing with large datasets.

### Performance and Size

* **Parsing Speed:** XML is a text-based format and can be slow to
  parse. Protobuf is a binary format that is designed for speed and
  efficiency. Converting XML data to Protobuf can result in significantly
  faster parsing times.
* **Storage Space:** XML is verbose, with opening and closing tags
  that add to the file size. Protobuf's binary format is much more
  compact, leading to smaller file sizes. This is a major advantage
  for storing large datasets or for transmitting data over a network.
  Effectively compressing XML requires a schema-specific dictionary,
  or compressing multiple records together so that the dictionary of
  tag/key names can be reused. However this limits the possibility
  for random access enabled by compressing records individually.

### Developer Experience

* **Generated Code:** Protobuf compilers generate code in many
  languages, providing a simple and consistent way to work with the
  data.
* **Type Safety:** The Protobuf schema provides strong typing, which
  can help to prevent bugs. Parsing the wire format requires the
  protobuf definition, meaning that the data is tied to its typed
  representation. Conversely XML representations are only optionally
  validated by a schema, and so by default type information is lost
  during parsing (everything is treated as text).

By converting XSDs to Protobuf definitions, `xsd-former` allows
developers to take advantage of the benefits of Protobuf while still
working with data that is originally defined in an XML schema.

## Output formats

From a single XSD (or DTD) source, XSDFormer can emit:

* **Protobuf** (`.proto`) — `--proto-out`.
* **Python XML→protobuf converters** — `--py-out`/`--py-module`.
* **JSON Schema** — `--json-schema-out` (from the XSD, or directly from
  a `.proto` via the `proto` subcommand).
* **TypeSpec** (`.tsp`) — `--typespec-out` (see below).

## TypeSpec output

[TypeSpec](https://typespec.io/) is a compact, authorable schema
language that fans out to OpenAPI, JSON Schema, and protobuf — and from
those to pydantic and zod. XSDFormer emits a `.tsp` so the same model
that backs the stored protobufs can also drive backend pydantic models
and frontend zod validators, all interconvertible.

```bash
# Default: clean, readable TypeSpec (string-valued enums, native scalars).
# Suited to pydantic/zod generation.
xsdformer xsd schema.xsd --typespec-out schema.tsp

# DTD sources work too (e.g. PubMed).
xsdformer dtd pubmed.dtd --typespec-out pubmed.tsp --proto-package pubmed

# proto-compat: adds @typespec/protobuf decorations (@package, @field,
# integer-valued enums) so `tsp -> proto` can be run as a regression
# check that it matches the directly generated proto.
xsdformer xsd schema.xsd --typespec-out schema.tsp --proto-compat
```

The `.tsp` is a derived artifact — regenerated from the XSD alongside
the proto, never hand-edited. The governing invariant is that
`xsd → proto` and `xsd → tsp → proto` agree at the wire/semantic level
(field numbers, field types, enum numbers); cosmetic differences
(`oneof` grouping, nested-vs-hoisted placement, comments, ordering) are
tolerated. See [`docs/adr/0001-typespec-output-format.md`](docs/adr/0001-typespec-output-format.md)
for the full design.
