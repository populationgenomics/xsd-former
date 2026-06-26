# 2. Pydantic codegen and the proto conversion hub

Date: 2026-06-26

## Status

Accepted

## Context

XSDFormer emits protobuf, a Python XML→protobuf converter, JSON Schema, and (ADR
0001) TypeSpec. Protobufs are live: they compactly store PubMed records. We now
want backend **pydantic** models as a first-class, code-generated artifact — and,
concretely, `../pubmed-proto` should ship a *full suite of PubMed conversions*
(parse, store, materialize, serialize) rather than just the proto half.

The sibling project `themis-internal` already solved **tsp → pydantic**:
`tools/schema/regen.py` runs `.tsp → @typespec/json-schema → normalize →
datamodel-code-generator → pydantic v2` (plus zod). That pipeline produces the
models; it does **not** produce converters, because themis has no XML and no
proto. The XML-binding information (element vs. attribute, wrapper/list structure,
content serialization) and the proto field numbers live *only* in XSDFormer's IR,
so any converter codegen must originate here.

### Governing constraint

The pydantic models XSDFormer emits must be **semantically equivalent** to what
the tsp→regen path produces — same fields, resolved types, optionality, defaults,
and enum member/value sets — so that proto-stored records, pydantic models, and
zod validators all describe the same data. Cosmetic differences (`Optional[T]`
vs `T | None`, `Field(default=…)` vs bare defaults, import order, comments) are
tolerated. This is the pydantic twin of ADR 0001's `xsd→proto ≡ xsd→tsp→proto`
wire/semantic invariant.

## Decision

### Generation route (direct from IR, oracle in CI)

Pydantic models are generated **directly from the IR** by a new
`src/xsdformer/pydantic/generator.py` (`PydanticGenerator`, `IGenerator` over
`Message`/`Enumeration`/`MapType` — a sibling of `ProtobufGenerator` and
`TypeSpecGenerator`). The tsp→regen→pydantic chain is demoted to an **equivalence
oracle** run in CI, exactly the role `xsd→tsp→proto` plays for proto.

Rejected: making `datamodel-code-generator` the production generator (option A).
It would force Node + dmcg into every `xsdformer build` invocation, and — fatally
— the proto↔pydantic converter would then have to match field/enum names dmcg
*invents*, requiring either fragile prediction or an import-and-introspect step in
the critical build path. Generating both pydantic and the converter from one IR
makes them correct by construction.

### Dialect

The pydantic dialect is ADR 0001's **default-mode tsp** rendered as pydantic v2:

- **Enums:** `str`-valued; member **name** = `EnumField.name` (the SCREAMING proto
  value name — the converter's identity key), member **value** = `xml_value` (the
  pretty JSON string). Synthesized `*_UNSPECIFIED = ""` zero member first.
- **Fields:** snake_case.
- **Scalars / maps / cardinality:** per ADR 0001 — `xs:date`→`datetime`,
  `MapType`→`dict[str, V]`, `(1,1)`→`T`, `(0,1)`→`T | None = None`, repeated→
  `list[T] = []`.
- **Nested types:** hoisted to module scope as `Parent_Child` (proto nests them;
  the converter bridges the two — see below).
- **Choice:** flattened to independent optional fields (no mutual-exclusion in the
  model — see "Choice enforcement").

### Conversion hub (proto-centric, hub-and-spoke)

Distinguish **representations** (in-memory typed objects: a proto message, a
pydantic model) from **serializations** (bytes/text emitted natively from a
representation: proto-wire, proto-JSON, `model_dump_json`). Serializations are
free — never generated. JSON is therefore *not* a converter node; zod/JSON Schema
are not instance converters at all, and the frontend joins via the shared
readable-JSON **contract**, not a Python converter.

**proto is the hub.** Every representation converts only to/from proto:

```
XML ──parse──▶ proto ◀──convert──▶ pydantic
                (hub)
```

- `XML → proto`: the existing, proven `xml_converter.py` (the one parser).
- `proto ↔ pydantic`: a new generated converter (below).
- `XML → pydantic`: the **composition** `XML → proto → pydantic`, not a generated
  artifact.

The rule that keeps converters **linear, not quadratic**: no direct
format↔format converter may bypass the proto hub; an Nth representation adds
exactly one spoke (`X ↔ proto`). proto stays the hub because it is the at-rest
source of truth; this flips only if proto stops being the persisted form.

### proto ↔ pydantic converter

A new generated `pydantic_converter.py` (emitted into the package beside
`*_pb2`), mechanical because both sides derive from one IR:

- **Enums:** keyed by `.name` (= proto value name), no remap table.
- **Timestamp ↔ datetime**, **maps ↔ dict**, **repeated ↔ list** — direct.
- **Nested↔hoisted:** proto `Parent.Child` ↔ pydantic `Parent_Child`.
- **Optional scalars:** via proto field presence — see R1.
- **Choice:** see enforcement below.

### R1 — optional-scalar presence

The protobuf generator will emit the proto3 `optional` keyword for `(0,1)`
**singular scalar and enum** fields. proto3 gives these no field presence:
absent is indistinguishable from default (`""`/`0`/`false`/the zero enum
member), so `pydantic→proto→pydantic` would collapse `None` on a `T | None`
field. `optional` gives `HasField`/`ClearField`, making proto↔pydantic lossless.
Enums are included for the same reason as scalars — the dialect renders an
optional enum as `MyEnum | None`, which needs a hasbit to round-trip. Left alone:
message-typed fields (including `xs:date`→`Timestamp`), maps, and repeated fields
— they already carry presence or have no None. Fields inside a proto `oneof` get
presence from the oneof and cannot carry `optional` (a proto3 syntax error), so
it is suppressed there.

Cost is negligible: wire-identical except when a field is *explicitly* set to its
default (then ~1–2 bytes to preserve presence); one hasbit per field in memory; a
synthetic single-member `oneof` in descriptor metadata. Wire- and
parse-compatible with existing stored bytes. The slice-6 round-trip normalizer
already discards proto3-optional markers, so the existing gate is unaffected.

### Choice enforcement

The pydantic model stays **permissive** (flat optionals, no validator) to remain
faithful to the regen oracle and consistent with zod/JSON Schema, which do not
carry the constraint. The `pydantic→proto` converter **raises** when more than one
branch of a proto `oneof` is set (proto cannot represent it). `proto→pydantic`
needs no check (proto guarantees ≤1). This accepts a known footgun — an invalid
multi-branch model is constructable in memory and only rejected at the proto
boundary — as the price of cross-runtime contract consistency. Escalation, *if*
at-most-one proves to be a domain invariant: a dialect-level discriminated union
enforced across **all** emitters, never a pydantic-only `model_validator`.

### Packaging / CLI

- `xsdformer build` emits pydantic **unconditionally**, beside the proto
  artifacts: `models.py` and `pydantic_converter.py`, with `pydantic` added to the
  generated package's dependencies and both modules re-exported from `__init__`.
- `--pydantic-out <path>` emits the **models module only** (clean dialect),
  mirroring `--typespec-out`. The converter is a `build`-package artifact only,
  since it is meaningful only beside the compiled `*_pb2`.

### Equivalence gate

Semantic, **not** a byte-diff (themis's freshness gate compares one generator
against itself; ours compares two different generators, which are never
byte-identical). Toolchain mirrors themis (`@typespec/json-schema` + `normalize` +
`datamodel-code-generator`), extending the slice-7 Node CI setup; nothing bespoke.

- **Primary assertion:** the IR-generated pydantic's induced JSON Schema
  (`Model.model_json_schema()`, normalized — sort keys, drop `title`/
  `description`) equals the tsp→`@typespec/json-schema` bundle. This tests
  equivalence to the **contract** (the source of truth), language-neutrally.
- **Fallback:** runtime introspection of `model_fields` (resolved annotation,
  `is_required()`, default) and enum `__members__`, for any axis JSON Schema does
  not capture (e.g. `default_factory`).
- `datamodel-code-generator` is kept as a freshness/sanity check, not the thing
  diffed against.

AST-based comparison is rejected: it operates at the syntactic layer and would
require hand-reimplementing the type canonicalization (`Optional[T]` ≡ `T | None`,
default forms, enum base classes) that the runtime and JSON Schema give for free.

## Consequences

- `xsdformer build` stays Python-only; Node + dmcg are needed only in CI for the
  gate, where the toolchain already lives.
- `../pubmed-proto` gains the full suite: `XML→proto` (store), `proto↔pydantic`
  (app boundary), pydantic↔JSON native. No standalone `XML→pydantic` until a
  proto-free consumer exists (same discipline ADR 0001 applied to camelCase).
- The proto generator's output changes: `(0,1)` singular scalars become
  `optional`. Wire-safe; existing stored records unaffected.
- Acceptance is a round-trip gate over real PubMed records: `XML→proto→pydantic→
  proto` is identical in the proto, and `proto→pydantic→JSON→pydantic` round-trips
  in pydantic.

## Implementation slices

Vertical, independently landable, each its own commit.

1. **PydanticGenerator + `--pydantic-out`.** ✅ Done. `PydanticGenerator`
   (`src/xsdformer/pydantic/generator.py`), a sibling of the protobuf/TypeSpec
   generators, emits clean-dialect pydantic v2: `str, Enum` enums (member name =
   proto value name, value = `xml_value`, `*_UNSPECIFIED = ""` first), snake_case
   fields with cardinality (`T` / `T | None = None` / `list[T] = []`), `MapType`→
   `dict[str, V] = {}`, `xs:date`→`datetime`, nesting hoisted to module scope as
   `Parent_Child`, and Choice→flat-optionals. Type docs become class docstrings;
   field docs are dropped (clean dialect — the gate normalizes descriptions).
   Python-keyword field names are `_`-suffixed with a `Field(alias=…)` and
   `populate_by_name`, matching the dmcg oracle. Imports are emitted only as
   used. `--pydantic-out` writes the models module (`xsd`/`dtd` commands).
   Backbone golden tests + a `compile()` syntax gate (`tests/xsdformer/pydantic/`);
   no pydantic runtime needed yet.
2. **R1 — proto3 `optional`.** ✅ Done. `ProtobufGenerator` emits `optional` for
   `(0,1)` singular scalar and enum fields (`_needs_proto3_optional`): excludes
   message-typed fields (incl. `xs:date`→`Timestamp`), maps, repeated, and `oneof`
   members. Descriptor- and text-level tests on the book fixture; the slice-7
   tsp→proto round-trip gate (book/ClinVar/PubMed) stays green — its normalizer
   already discards proto3-optional markers (only `LABEL_REPEATED` is compared).
3. **proto↔pydantic converter.** ✅ Done. `src/xsdformer/pydantic/converter.py`
   emits `pydantic_converter.py`: a `*_from_proto`/`*_to_proto` pair per message,
   driven by the same IR signals as the pydantic generator (cardinality, Choice
   flattening, keyword aliasing) and the protobuf generator (`oneof` formation,
   proto3 `optional` presence). Optional scalars/enums round-trip via `HasField`
   (R1); enums key by member name (`Model[Proto.Name(v)]` / `Proto.Value(.name)`,
   no remap); `Timestamp`↔`datetime` via `ToDatetime`/`FromDatetime`; maps→`dict`,
   repeated→`list`; nested proto `Parent.Child`↔hoisted `Parent_Child`; keyword
   proto fields reached via `getattr`/`setattr`. `pydantic→proto` raises when more
   than one branch of a proto `oneof` is set; `proto→pydantic` needs no check.
   Backbone golden + `compile()` tests (`tests/xsdformer/pydantic/test_converter.py`);
   no pydantic runtime needed yet.
4. **build integration.** ✅ Done. `build_package` emits `models.py`
   (`PydanticGenerator`) and `pydantic_converter.py` (`PydanticConverterGenerator`,
   wired to `{package}.{namespace}_pb2` and `{package}.models`) beside the proto
   artifacts, unconditionally. `pydantic>=2` added to the generated package's
   `dependencies`, and both modules re-exported from `__init__`. Tests
   (`tests/xsdformer/test_build.py`): file-tree/pyproject/`__init__` assertions plus
   a subprocess package-import check and a live `proto→pydantic→proto` round-trip
   over the book fixture (`pydantic` is now a dev dependency).
5. **Equivalence gate (gated, Node + dmcg).** ✅ Done. `tests/xsdformer/pydantic/
   test_equivalence.py` asserts the IR-generated pydantic's induced JSON Schema
   (`model_json_schema()`) equals the default-mode tsp compiled by
   `@typespec/json-schema` (new `_tsp.compile_tsp_to_json_schema`, `emitAllModels`
   + `int64-strategy=number` to keep 64-bit ints native per ADR 0001). Both sides
   reduce to a canonical `$defs` map (`_equivalence.py`): refs localized (#4084),
   `title`/`description`/`default`/`$id`/`$schema` and integer-width
   (`format`/`minimum`/`maximum`) dropped, pydantic's `anyOf: [T, null]`
   nullability unwrapped, and array-/map-typed fields excluded from `required`
   (`T[]` required-but-empty ≡ `list[T] = []`). Gated on
   `_tsp.json_schema_available()`; green over book/ClinVar/PubMed under the
   production transforms. `datamodel-code-generator` (new dev dep) is a
   freshness/sanity check (`test_datamodel_codegen_sanity` — dmcg consumes the
   contract and the models import), not the thing diffed against. CI's existing
   `npm install` now also pulls `@typespec/json-schema`.
6. **Real schemas + `pubmed-proto`.** ✅ Done. `../pubmed-proto`'s `make generate`
   now emits the full suite (`models.py` + `pydantic_converter.py` beside the proto
   artifacts) — no Makefile change needed; `xsdformer build` (slice 4) already does
   so unconditionally, and `generated/` is a gitignored build artifact. The
   acceptance gate lives in `tests/xsdformer/pydantic/test_roundtrip.py`: it builds
   the pubmed suite from `tests/.../schemas/pubmed.dtd` under `pubmed_transforms.yaml`
   via `build_package`, then over real NLM PubMed records (`records/*.xml`, `efetch`
   output) asserts `XML→proto→pydantic→proto` is identical in the proto and
   `proto→pydantic→JSON→pydantic` round-trips in pydantic — exercising the whole
   generated suite (XML converter, compiled `*_pb2`, models, proto↔pydantic
   converter) end to end. Each record round-trips in a subprocess to isolate the
   global descriptor-pool registration, matching the slice-4 build checks.
