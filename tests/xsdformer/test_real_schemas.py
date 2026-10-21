"""`build_package` over the real ClinVar XSD and PubMed DTD, under their fixture transforms.

These schemas carry what the book fixture lacks: `xs:date`/`Timestamp`, deep
nesting, nested enums, markdown content serialization, and the transform passes
at the scale of a real schema. The records in `records/` are real NLM PubMed XML
(`efetch` output).
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from xsdformer import build, transforms
from xsdformer.dtd import dtd
from xsdformer.xsd import xsd

_FIXTURES_DIR = pathlib.Path(__file__).parent
_SCHEMAS_DIR = _FIXTURES_DIR / 'schemas'
_RECORDS = sorted((_FIXTURES_DIR / 'records').glob('*.xml'))


def _run(script: str) -> None:
    # A subprocess keeps the compiled `*_pb2` (a global descriptor-pool
    # registration) out of the test process.
    result = subprocess.run(  # noqa: S603
        [sys.executable, '-c', script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture(scope='module')
def clinvar_package(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    config = transforms.TransformConfig.from_yaml(_SCHEMAS_DIR / 'clinvar_transforms.yaml')
    type_defs = transforms.apply_transforms(xsd.process_xsd(str(_SCHEMAS_DIR / 'ClinVar_VCV.xsd')), config)
    out_dir = tmp_path_factory.mktemp('clinvar_build')
    build.build_package(type_defs=type_defs, namespace='clinvar', package_name='clinvar_proto', out_dir=out_dir)
    return out_dir


@pytest.fixture(scope='module')
def pubmed_package(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    config = transforms.TransformConfig.from_yaml(_SCHEMAS_DIR / 'pubmed_transforms.yaml')
    type_defs = transforms.apply_transforms(dtd.process_dtd(str(_SCHEMAS_DIR / 'pubmed.dtd')), config)
    out_dir = tmp_path_factory.mktemp('pubmed_build')
    build.build_package(type_defs=type_defs, namespace='pubmed', package_name='pubmed_proto', out_dir=out_dir)
    return out_dir


def test_clinvar_package_imports(clinvar_package: pathlib.Path) -> None:
    # The package `__init__` imports the compiled `_pb2` and the XML converter.
    _run(f'import sys; sys.path.insert(0, {str(clinvar_package)!r}); import clinvar_proto')


def test_records_present() -> None:
    assert _RECORDS, 'no PubMed record fixtures found'


@pytest.mark.parametrize('record', _RECORDS, ids=lambda p: p.stem)
def test_pubmed_record_converts(record: pathlib.Path, pubmed_package: pathlib.Path) -> None:
    # Every article in the set comes out, under the PMID and with the title the XML carries.
    _run(f"""
import sys
sys.path.insert(0, {str(pubmed_package)!r})
from lxml import etree
from pubmed_proto import xml_converter

root = etree.parse({str(record)!r}).getroot()
article_els = root.findall('PubmedArticle')
assert article_els, 'no PubmedArticle in the record'
record_set = xml_converter.PubmedArticleSet(root)
assert len(record_set.pubmed_article) == len(article_els)
for proto, article_el in zip(record_set.pubmed_article, article_els, strict=True):
    citation = proto.medline_citation
    assert citation.pmid.value == article_el.findtext('MedlineCitation/PMID')
    assert citation.article.article_title.value
""")
