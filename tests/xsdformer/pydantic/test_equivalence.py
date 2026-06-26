"""Equivalence gate: IR-generated pydantic ≡ tsp contract (ADR 0002 slice 5).

The governing constraint: the pydantic models XSDFormer emits must be semantically
equivalent to what the tsp→regen path produces — same fields, resolved types,
optionality, defaults, and enum member/value sets. This gate tests that
language-neutrally, at the **contract** (JSON Schema) layer:

- LHS: the IR-generated pydantic models' induced JSON Schema
  (``Model.model_json_schema()``), per ADR 0002's "Primary assertion".
- RHS: the same IR rendered as default-mode TypeSpec and compiled by
  ``@typespec/json-schema`` — the contract that is the source of truth.

Both are reduced to a canonical ``$defs`` map (see ``_equivalence``) that cancels
tolerated cosmetic differences, then compared. ``datamodel-code-generator`` is the
freshness/sanity check (``test_datamodel_codegen_sanity``), not the thing diffed
against — the ADR diffs against the contract, not against dmcg's invented output.

Gated on the Node toolchain (``json_schema_available``), like the slice-7
tsp→proto round-trip; skips otherwise.
"""

from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from tests.xsdformer.conftest import _BOOK_XSD
from tests.xsdformer.pydantic import _equivalence
from tests.xsdformer.typespec import _tsp
from xsdformer.dtd import dtd
from xsdformer.pydantic import generator as pydantic_generator
from xsdformer.transforms import TransformConfig, apply_transforms
from xsdformer.typespec import generator as typespec_generator
from xsdformer.xsd import xsd

if TYPE_CHECKING:
    import types

pytestmark = pytest.mark.skipif(
    not _tsp.json_schema_available(),
    reason="TypeSpec toolchain unavailable (run `npm install` in tests/xsdformer/typespec/tsp_project)",
)

_SCHEMAS_DIR = pathlib.Path(__file__).parents[1] / "typespec" / "schemas"
_REPO_ROOT = pathlib.Path(__file__).parents[3]


def _load_module(code: str, name: str, tmp_path: pathlib.Path) -> types.ModuleType:
    """Load generated pydantic source as an importable module."""
    path = tmp_path / f"{name}.py"
    path.write_text(code)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Stay in `sys.modules`: with `from __future__ import annotations`, pydantic
    # resolves forward references (hoisted nested types reference each other in
    # any order) lazily against the module's globals, which it finds via
    # `sys.modules[__module__]`. Unique names keep tests from colliding.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _assert_equivalent(
    type_defs: tuple[xsd.TypeDefinition, ...],
    namespace: str,
    tmp_path: pathlib.Path,
) -> None:
    """Assert IR-pydantic's induced JSON Schema ≡ the tsp→json-schema bundle."""
    tsp_source = "\n".join(typespec_generator.generate(namespace, type_defs))
    bundle = _tsp.compile_tsp_to_json_schema(tsp_source, tmp_path)
    contract = _equivalence.canonicalize(_equivalence.tsp_defs(bundle))

    module = _load_module(
        "\n".join(pydantic_generator.generate(namespace, type_defs)),
        f"{namespace}_models",
        tmp_path,
    )
    induced = _equivalence.canonicalize(_equivalence.pydantic_defs(module))

    assert induced == contract


def test_book_equivalent(tmp_path: pathlib.Path) -> None:
    """book: IR-pydantic ≡ tsp contract (backbone fixture)."""
    _assert_equivalent(xsd.process_xsd(io.StringIO(_BOOK_XSD)), "book", tmp_path)


def test_clinvar_equivalent(tmp_path: pathlib.Path) -> None:
    """ClinVar: IR-pydantic ≡ tsp contract under the production transforms."""
    config = TransformConfig.from_yaml(_REPO_ROOT / "clinvar_transforms.yaml")
    type_defs = apply_transforms(xsd.process_xsd(str(_SCHEMAS_DIR / "ClinVar_VCV.xsd")), config)
    _assert_equivalent(type_defs, "clinvar", tmp_path)


def test_pubmed_equivalent(tmp_path: pathlib.Path) -> None:
    """PubMed: IR-pydantic ≡ tsp contract under the production transforms."""
    config = TransformConfig.from_yaml(_REPO_ROOT / "pubmed_transforms.yaml")
    type_defs = apply_transforms(dtd.process_dtd(str(_SCHEMAS_DIR / "pubmed.dtd")), config)
    _assert_equivalent(type_defs, "pubmed", tmp_path)


@pytest.mark.skipif(
    importlib.util.find_spec("datamodel_code_generator") is None,
    reason="datamodel-code-generator not installed",
)
def test_datamodel_codegen_sanity(tmp_path: pathlib.Path) -> None:
    """Freshness/sanity check: dmcg turns the tsp contract into models that import.

    Mirrors themis's regen path (``@typespec/json-schema`` → normalize → dmcg) as a
    smoke test that the contract is dmcg-consumable; the ADR keeps dmcg as a sanity
    check, *not* the thing diffed against (its invented names would force fragile
    prediction). Equivalence is asserted against the contract above.
    """
    type_defs = xsd.process_xsd(io.StringIO(_BOOK_XSD))
    bundle = _tsp.compile_tsp_to_json_schema("\n".join(typespec_generator.generate("book", type_defs)), tmp_path)
    # #4084: localize the bundle's `$id`-relative refs so dmcg resolves them.
    normalized = _equivalence.canonicalize(_equivalence.tsp_defs(bundle))
    schema_path = tmp_path / "book.schema.json"
    schema_path.write_text(json.dumps({"$defs": normalized}))
    out_path = tmp_path / "dmcg_models.py"
    subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "datamodel_code_generator",
            "--input",
            str(schema_path),
            "--input-file-type",
            "jsonschema",
            "--output",
            str(out_path),
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-union-operator",
            "--use-standard-collections",
        ],
        check=True,
    )
    _load_module(out_path.read_text(), "book_dmcg", tmp_path)
