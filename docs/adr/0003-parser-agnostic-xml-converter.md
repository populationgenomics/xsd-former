# 3. Parser-agnostic generated XML converter

Date: 2026-07-17

## Status

Accepted

## Context

The generated Python XML→protobuf converter (`py/xml_converter.py`) is written
against the ElementTree contract — `.tag`, `.text`, `.tail`, iteration,
`.find()` — which lxml, stdlib `xml.etree.ElementTree`, and
`defusedxml.ElementTree` all implement. Consumers should be free to pick the
parser: hardened lxml for speed, or defusedxml/stdlib for untrusted input
(defusedxml parses to stdlib elements). Two things forced lxml regardless:

1. **Type annotations** named `lxml.etree._Element`, so a consumer type-checking
   a stdlib/defused element against the generated functions saw a mismatch.
2. **Runtime coupling** in `_xml_as_str` (the `xsd:any`/COMPLEXANY serializer),
   which called `lxml.etree.tostring`. lxml's `tostring` rejects non-lxml
   elements, and `build.py` listed `lxml` as a hard dependency of every
   generated package.

Annotation lock-in alone is cheap to remove; the runtime call is the part that
would actually break a defused/stdlib element at runtime.

## Decision

The generated converter is parser-agnostic. The consumer supplies elements from
any ElementTree-compatible parser.

- **Annotations use a structural `_Element` Protocol** (`tag`/`text`/`tail`/
  `find`/`__iter__`), copied verbatim into generated modules via
  `inspect.getsource` alongside the runtime helpers. Rejected: annotating as
  stdlib `ElementTree.Element` — lxml `_Element` is not a subclass, so that
  merely reverses the lock-in (a consumer passing lxml elements would fail
  type-checking). A Protocol accepts all three parsers honestly.
- **`_xml_as_str` serializes with stdlib `ElementTree.tostring`**, which
  duck-types over lxml, stdlib, and defusedxml elements alike (verified);
  lxml's does not. A single `cast` bridges the Protocol to the concretely-typed
  stdlib serializer. Serialization (unlike parsing) carries no XXE risk, so
  using stdlib here is safe regardless of the input parser.
- **Generated modules no longer import lxml**; they gain
  `from __future__ import annotations` (matching house style) and import
  `xml.etree.ElementTree` (stdlib) only for serialization.
- **`build.py` drops `"lxml"` from generated `dependencies`.** The consumer
  declares whichever parser it uses.

## Consequences

- Generated packages that previously relied on lxml arriving transitively must
  now declare their own parser dependency. This is the intended contract — the
  package no longer uses lxml at all — but it is a consumer-visible change.
- `test_xml_to_proto_stdlib_parser` drives the book converter with a
  stdlib-parsed element (exercising the `_xml_as_str`/`tostring` path via the
  COMPLEXANY `metadata` field) to guard the runtime invariant.
