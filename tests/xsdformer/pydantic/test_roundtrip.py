"""Round-trip gate over real PubMed records (ADR 0002 slice 6).

The ADR's acceptance criterion: over real PubMed records under the production
transform config, ``XML→proto→pydantic→proto`` is identical in the proto, and
``proto→pydantic→JSON→pydantic`` round-trips in pydantic. This exercises the full
generated suite end to end — the XML converter, the compiled ``*_pb2``, the
pydantic models, and the proto↔pydantic converter — built exactly as
``xsdformer build`` ships it for ``../pubmed-proto``.

The fixtures in ``records/`` are real NLM PubMed XML records (``efetch`` output).
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from xsdformer.build import build_package
from xsdformer.dtd import dtd
from xsdformer.transforms import TransformConfig, apply_transforms

_REPO_ROOT = pathlib.Path(__file__).parents[3]
_SCHEMAS_DIR = _REPO_ROOT / 'tests' / 'xsdformer' / 'typespec' / 'schemas'
_RECORDS_DIR = pathlib.Path(__file__).parent / 'records'
_RECORDS = sorted(_RECORDS_DIR.glob('*.xml'))


@pytest.fixture(scope='module')
def pubmed_package(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """Build the full pubmed suite once, under the production transform config."""
    config = TransformConfig.from_yaml(_REPO_ROOT / 'pubmed_transforms.yaml')
    type_defs = apply_transforms(dtd.process_dtd(str(_SCHEMAS_DIR / 'pubmed.dtd')), config)
    out_dir = tmp_path_factory.mktemp('pubmed_build')
    build_package(type_defs=type_defs, namespace='pubmed', package_name='pubmed_proto', out_dir=out_dir)
    return out_dir


def test_records_present() -> None:
    assert _RECORDS, f'no PubMed record fixtures found in {_RECORDS_DIR}'


@pytest.mark.parametrize('record', _RECORDS, ids=lambda p: p.stem)
def test_pubmed_record_roundtrip(record: pathlib.Path, pubmed_package: pathlib.Path) -> None:
    # Run in a subprocess so the dynamically compiled `*_pb2` (a global descriptor
    # pool registration) and the generated package stay isolated from the test
    # process — matching the build-package import/round-trip checks.
    script = f"""
import sys
sys.path.insert(0, {str(pubmed_package)!r})
from lxml import etree
from pubmed_proto import xml_converter, pydantic_converter, models

tree = etree.parse({str(record)!r})
article_el = tree.getroot().find("PubmedArticle")
assert article_el is not None
proto = xml_converter.PubmedArticle(article_el)

# XML -> proto -> pydantic -> proto is identical in the proto.
model = pydantic_converter.PubmedArticle_from_proto(proto)
assert pydantic_converter.PubmedArticle_to_proto(model) == proto

# proto -> pydantic -> JSON -> pydantic round-trips in pydantic.
restored = models.PubmedArticle.model_validate_json(model.model_dump_json())
assert restored == model
"""
    result = subprocess.run(  # noqa: S603
        [sys.executable, '-c', script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
