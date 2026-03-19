# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

XSDFormer converts XML Schema Definitions (XSD) into:
1. **Protobuf definitions** (.proto files)
2. **Python XML-to-protobuf converters** (generated Python code that parses XML into protobuf instances)
3. **JSON schemas** (from XSD or directly from .proto files)

It supports enough of XSD to handle ClinVar and BioC schemas; full XSD support is a non-goal.

## Commands

```bash
# Install dependencies (uses uv)
uv sync --dev

# Run all tests
uv run pytest

# Run a single test
uv run pytest tests/xsdformer/xsd/test_xsd.py::test_name -x

# Lint
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/

# Auto-fix lint issues
uv run ruff check --fix src/ tests/
uv run ruff format src/ tests/
```

## Architecture

### Pipeline

The core pipeline is: **XSD parsing -> intermediate representation -> code generation**.

1. `xsd/xsd.py` — Parses XSD via `xmlschema`, topologically sorts types by dependency, and produces an IR of `TypeDefinition` objects (`Message`, `Enumeration`, `MapType`).
2. `generator.py` — Defines the `IGenerator` protocol and `generate_with()`, which drives any generator over the IR, skipping enclosed (nested) types at the top level.
3. Three generators consume the IR:
   - `protobuf/generator.py` — Emits `.proto` syntax.
   - `py/xml_converter.py` — Emits Python code that converts lxml elements to protobuf instances. Helper functions (e.g. `_xml_bool`, `_consume`) are embedded in generated output via `inspect.getsource`.
   - `jsonschema/generator.py` — Compiles proto (from IR or a `.proto` file) via `grpc_tools.protoc` into a `FileDescriptorSet`, then walks protobuf descriptors to emit JSON Schema.

### IR types (`xsd/xsd.py`)

- `TypeDefinition` — base for `Message`, `Enumeration`, `MapType`.
- `FieldDefinition` — base for `Field` subtypes (`Elem`, `Attr`, `ValueElem`) and containers (`Seq`, `Choice`).
- `Field.computed_occurs` — the effective occurrence after multiplying through nested containers.
- `TypeDefinition.enclosing_type` — links nested types to their parent message. `path` is the tuple of names from root to the type.

### CLI (`tool.py`)

Two Click commands:
- `xsdformer xsd <file>` — XSD-based pipeline (proto, python converter, and/or JSON schema output).
- `xsdformer proto <file> <namespace>` — Proto-to-JSON-schema only.

### Text utilities (`xsd/text.py`)

`snake_case`, `pascal_case`, `keep` (wraps a string in `_Exact` to bypass case conversion). All identifier naming flows through these.

### Test fixtures (`tests/xsdformer/conftest.py`)

`pb2_module_factory` and `py_converter_module_factory` compile XSD to protobuf Python modules at test time via `grpc_tools.protoc`, then dynamically load them. Tests use a shared "book" XSD schema fixture.
