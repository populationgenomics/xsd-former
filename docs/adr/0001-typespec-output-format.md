# 1. TypeSpec (`.tsp`) as an output format

Date: 2026-06-19

## Status

Accepted

## Context

XSDFormer currently emits protobuf, a Python XML→protobuf converter, and JSON
Schema. Protobufs are already live: they compactly store PubMed records.

For ad-hoc use we want to serialize those records to JSON, materialize them as
backend pydantic models, and validate them in the frontend with zod — and we
want all of these to be interconvertible with the stored protobufs.

TypeSpec is a compact, authorable schema language that fans out to OpenAPI, JSON
Schema, protobuf, and (via those) pydantic and zod. It fills the niche XSDFormer
doesn't already cover: a single editable model that drives the
non-protobuf world. This ADR adds `.tsp` emission.

### Governing constraint

`xsd→proto` must equal `xsd→tsp→proto` at the **wire/semantic** level — same
field numbers, field types, and enum numbers — so that proto-stored records and
tsp-derived pydantic/zod describe the same data. Cosmetic differences (`oneof`
grouping, nested-vs-hoisted type placement, comments, field ordering) are
tolerated: they do not affect the wire format or proto-JSON.

## Decision

### Lifecycle (L1, always)

XSD remains the source of truth. The `.tsp` is a **derived artifact**,
regenerated from XSD alongside the proto. Nobody hand-edits it. Consistency
between proto and tsp is therefore automatic (one IR), and `xsd→tsp→proto` is a
regression check, not a production path. Proto-compatibility is opt-in.

### Generator

- New `src/xsdformer/typespec/generator.py`: `TypeSpecGenerator` implementing the
  `IGenerator` protocol (singledispatch over `Message`/`Enumeration`/`MapType`),
  a sibling of `ProtobufGenerator`. Sources from the **IR**, not from proto
  descriptors — the IR carries field numbers, `Choice`, `MapType`, documentation,
  and the richer `AtomicType` enum that proto3 loses.

### Mappings

- **Enums** carry both representations so a single artifact serves every emitter:
  member name = `EnumField.name` (the SCREAMING proto value name), string value =
  `xml_value`. A synthesized `*_UNSPECIFIED: ""` zero member comes first, in
  xsdformer declaration order. Result: pydantic exposes `.name`
  (= proto value name, the converter's identity key) and `.value` (= pretty JSON
  string). No remap table needed in the proto↔pydantic converter.
- **Fields:** snake_case (Python-idiomatic). No naming-style flag until a second,
  JS-first consumer actually needs camelCase.
- **Choice:** flattened to optional properties (default mode). A proto `oneof`
  and N optional fields with the same numbers are wire- and proto-JSON-identical,
  so this satisfies the invariant.
- **Nested (enclosed) types:** hoisted to the top-level namespace, named
  `Parent_Child` — PascalCase components joined by `_`. Collision-free by
  construction (`pascal_case` strips underscores), so the separator is an
  unambiguous, reversible path delimiter.
- **Maps:** a field whose `proto_type` is a `MapType` becomes `Record<ValueType>`
  (string-keyed — proto-JSON stringifies all map keys, so this is JSON-correct for
  every proto map). A top-level `MapType` emits nothing. Non-string proto key
  types are not currently exercised; deferred.
- **Scalars** (`AtomicType` → TypeSpec): `ID`/`STRING`/`URI`/`SIMPLEANY`/
  `COMPLEXANY` → `string`; `INT8…UINT64` → native `int8`…`uint64`; `FLOAT` →
  `float32`; `DOUBLE` → `float64`; `BOOL` → `boolean`; `BYTES` → `bytes`; `DATE`
  → `utcDateTime`. `int64`/`uint64` stay native integers (JSON numbers, not
  strings) — proto-JSON's string encoding of 64-bit ints is bridged by the
  converter; JS loses precision above 2^53 but real IDs (e.g. PMIDs) are well
  under that.
- **Cardinality** (`computed_occurs`): `(1,1)` → `T`; `(0,1)` → `T?`; repeated →
  `T[]` (required-but-possibly-empty, i.e. pydantic `list[T] = []`).
- **Documentation:** `/** … */` doc-comments (new `render_doc_comment` in
  `text.py`), which emitters promote to descriptions automatically.

### File / CLI

- Single `.tsp` file, one `namespace` derived from the same resolution as proto's
  `package` (`--proto-package` or filename stem).
- `--typespec-out <path>`; `--proto-compat` (boolean). `--proto-compat` without
  `--typespec-out` is a `UsageError`. No `--main-message` (TypeSpec emits all
  types). tsp follows the existing output-flag pattern; no stdout special-casing.
- Default mode: clean, readable, no proto decorators. `--proto-compat`: adds
  `import "@typespec/protobuf"; using Protobuf;`, `@package` on the namespace, and
  `@field`/enum decorations so `tsp→proto` can be run as the regression check.

### Testing

- **Backbone (pure Python):** golden/structural assertions on the emitted `.tsp`.
- **Gated (Node, CI only):** `skipif`-gated on the `tsp` CLI — (i) the emitted tsp
  compiles, and (ii) the normalized-descriptor round-trip `xsd→proto` ≡
  `xsd→tsp→proto` (field numbers, types, enum numbers). The `xsd→proto→descriptor`
  half reuses the existing `grpc_tools.protoc` path.

## Consequences

- App/frontend speak the readable (pydantic/tsp) JSON dialect; raw proto-JSON is
  never shipped directly to the frontend (it can't be, given readable enums).
- A proto↔pydantic converter must exist; it is mechanical (enum mapping by
  `.name`, field mapping by name) and can be generated.
- The default `.tsp` intentionally diverges from the existing descriptor-based
  JSON Schema generator (readable enum values vs SCREAMING `value.name`). That is
  acceptable and expected under this design.
- One open risk, **resolved by slice 1**: `@typespec/protobuf` (compiler 1.13.0,
  protobuf emitter 0.83.0) does *not* accept string-valued enum members, and does
  *not* auto-number bare members — it requires every member to carry an explicit
  integer with a zero first member (`unconvertible-enum` otherwise). So
  `--proto-compat` (slice 6) emits integer-valued enums (member *names* preserved,
  numbers = the IR's enum numbers); proto carries no string value anyway, so the
  round-trip check is unaffected. The default, non-proto-compat `.tsp` keeps
  readable string values (TypeSpec-native, fine for pydantic/zod).

## Implementation slices

Vertical, independently landable, each its own commit.

1. **Spike / Node test harness.** ✅ Done. Stood up the `skipif`-gated round-trip
   harness (`tests/xsdformer/typespec/`, toolchain in `tsp_project/`) against a
   one-enum schema. Outcome: `@typespec/protobuf` rejects `SOME_NAME_VALUE:
   "value"` (and bare members) — it requires explicit integers with a zero first
   member. Slice 6's `--proto-compat` enums therefore use integer values; default
   mode keeps string values.
2. **Walking skeleton.** ✅ Done. `TypeSpecGenerator`
   (`src/xsdformer/typespec/generator.py`) over the `IGenerator` protocol:
   namespace (PascalCased per dotted component) + flat `model`s with scalar
   fields (snake_case, reused from the IR) and cardinality (`T`/`T?`/`T[]`).
   `--typespec-out` wired into `tool.py`. Backbone golden tests in
   `tests/xsdformer/typespec/test_generator.py`. `Enumeration`/`MapType`
   definitions emit nothing (deferred to slices 3/5); no nesting, `Choice`, or
   proto-compat yet.
3. **Enums + docs.** ✅ Done. String-valued `enum` emission (member name =
   `EnumField.name`, value = `xml_value`, synthesized `*_UNSPECIFIED: ""` zero
   member first) and `render_doc_comment` (`text.py`) promoting XSD
   documentation to JSDoc-style `/** … */` comments on models, fields, and
   enums. Backbone golden tests in `test_generator.py`.
4. **Nesting + Choice.** ✅ Done. Enclosed types are hoisted to the top-level
   namespace under their `Parent_Child` path-joined name (`_type_name`; `generate`
   no longer skips enclosed types the way the proto/JSON-Schema generators do),
   and `Choice` members flatten to optional properties (`_iter_message_fields`
   tracks `Choice` enclosure, forcing `T?` regardless of a branch's own minimum).
   Backbone golden tests in `test_generator.py`.
5. **Maps.** `Record<ValueType>`.
6. **proto-compat mode.** `--proto-compat` flag, imports/`using`, `@package`,
   `@field`, enum handling per slice 1's result; full normalized-descriptor
   round-trip across the book fixture.
7. **Real schemas + docs.** Run over `clinvar`/`pubmed`; update README; ensure CI
   installs the Node toolchain for the gated tests.
