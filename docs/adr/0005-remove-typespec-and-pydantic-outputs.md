# 5. Remove the TypeSpec and pydantic outputs

Date: 2026-10-22

## Status

Accepted. Supersedes [ADR 0001](0001-typespec-output-format.md) and
[ADR 0002](0002-pydantic-codegen-and-proto-hub.md), except ADR 0002's R1, which
this ADR keeps.

## Context

ADR 0001 added a TypeSpec (`.tsp`) output so one model could drive pydantic and
zod beside the stored protobufs. ADR 0002 added pydantic models and a
proto↔pydantic converter, generated directly from the IR, with the
TypeSpec-derived JSON Schema as their equivalence oracle, and made `xsdformer
build` put both modules in every generated package.

Neither output has a user. No consumer passes `--typespec-out`; the `.tsp` served
only ADR 0001's round-trip check and ADR 0002's oracle. The pydantic modules in
the generated packages are used only by one stale smoke test in Themis, which
stores binary protos. Keeping them costs a Node toolchain in CI, a `pydantic`
requirement in every generated package, and a second and third emitter that every
IR change has to keep in step.

## Decision

Remove both outputs in 2.0.0:

- `src/xsdformer/typespec/`, `--typespec-out` and `--proto-compat`, and the
  Node-gated round-trip tests with their npm harness.
- `src/xsdformer/pydantic/`, `--pydantic-out`, the generated `models.py` and
  `pydantic_converter.py`, and the generated package's `pydantic>=2` requirement.
  `pydantic` is no longer reserved in `build.dependencies`.

Kept: the protobuf generator, the Python XML→protobuf converter, JSON Schema, the
`build` package (proto, `_pb2`, XML converter), and ADRs 0003 and 0004.

Two pieces of the removed work outlive the emitters:

- **R1, proto3 `optional` on `(0,1)` singular scalar and enum fields.** Without a
  hasbit a reader cannot tell an omitted XML element or attribute from one that
  carried the type default; `HasField` can. Dropping it would change every
  generated `.proto` and remove `HasField` from fields consumers may call it on.
- **The conflicting-duplicate check (`xsd.register_field`),** added with the
  TypeSpec emitter and shared by the pydantic ones. They raised when a field name
  recurs across Choice branches with a different type or cardinality, where the
  protobuf generator kept the first branch's field. `build` ran the pydantic
  emitters on every package, so it already refused such schemas; the protobuf
  generator now calls the check itself.

The ClinVar XSD and PubMed DTD fixtures, and the PubMed records, move to
`tests/xsdformer/schemas/` and `tests/xsdformer/records/`;
`test_real_schemas.py` builds both schemas and converts the records, the
real-schema coverage the removed round-trips carried.

## Consequences

- 2.0.0 is a breaking release: two CLI options of `xsd` and `dtd` are gone, and
  packages built with it no longer contain `models` or `pydantic_converter`.
  pubmed-proto and clinvar-proto must drop their pydantic round-trips and the
  module rows in their READMEs when they adopt it.
- A consumer that wants pydantic models generates them from the `.proto` or the
  JSON Schema output with a third-party tool.
- `xsdformer xsd`/`dtd` with `--proto-out` or `--json-schema-out` now raise on a
  Choice whose branches share a field name with a different type or cardinality.
  1.x emitted the first branch's field. Valid XSD can trigger it: a branch
  `a` and a branch `b, a*`.
- Generated packages no longer pull in `pydantic` transitively; a consumer that
  relied on that declares it. Themis's smoke test of the pydantic modules breaks
  when Themis takes a pubmed-proto or clinvar-proto built with 2.0.0.
- CI no longer installs Node.
