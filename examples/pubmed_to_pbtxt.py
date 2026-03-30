"""Convert PubMed XML articles to .pbtxt using the transform pipeline."""

import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import types

from google.protobuf import text_format
from lxml import etree

from xsdformer.dtd import dtd
from xsdformer.protobuf import generator as proto_gen
from xsdformer.py import xml_converter
from xsdformer.transforms import TransformConfig, apply_transforms


def main() -> None:
    dtd_path = pathlib.Path(sys.argv[1])
    xml_paths = [pathlib.Path(p) for p in sys.argv[2:]]

    transforms_path = pathlib.Path(__file__).parent.parent / "pubmed_transforms.yaml"
    config = TransformConfig.from_yaml(transforms_path)

    type_defs = dtd.process_dtd(str(dtd_path))
    type_defs = apply_transforms(type_defs, config)

    namespace = "pubmed"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)

        # Generate and compile proto.
        proto_def = "\n".join(proto_gen.generate(namespace, type_defs))
        proto_path = tmp_path / f"{namespace}.proto"
        proto_path.write_text(proto_def)

        spec = importlib.util.find_spec("google.protobuf.timestamp_pb2")
        proto_include = pathlib.Path(spec.origin).parent.parent

        subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-m",
                "grpc_tools.protoc",
                f"--proto_path={tmp_path}",
                f"--proto_path={proto_include}",
                f"--python_out={tmp_path}",
                str(proto_path.relative_to(tmp_path)),
            ],
            check=True,
        )

        # Load compiled proto module.
        pb2_spec = importlib.util.spec_from_file_location(
            f"{namespace}_pb2",
            tmp_path / f"{namespace}_pb2.py",
        )
        module_pb2 = importlib.util.module_from_spec(pb2_spec)
        pb2_spec.loader.exec_module(module_pb2)

        # Generate and load converter.
        sys.modules[module_pb2.__name__] = module_pb2
        converter_code = "\n".join(
            xml_converter.generate(namespace, type_defs, module_pb2.__name__),
        )
        converter = types.ModuleType("pubmed_converter")
        exec(converter_code, converter.__dict__)  # noqa: S102

        # Convert each XML file.
        for xml_path in xml_paths:
            tree = etree.parse(str(xml_path))
            root = tree.getroot()

            # Handle PubmedArticleSet wrapper or single PubmedArticle.
            if root.tag == "PubmedArticleSet":
                articles = root.findall("PubmedArticle")
            elif root.tag == "PubmedArticle":
                articles = [root]
            else:
                print(f"Skipping {xml_path}: unknown root element {root.tag}")
                continue

            for article_el in articles:
                proto = converter.PubmedArticle(article_el)
                pmid = proto.medline_citation.pmid.value
                print(f"# PMID: {pmid}")
                print(text_format.MessageToString(proto))


if __name__ == "__main__":
    main()
