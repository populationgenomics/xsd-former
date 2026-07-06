"""Convert ClinVar VCV XML entries to .pbtxt using the transform pipeline."""

import gzip
import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import types

from google.protobuf import text_format
from lxml import etree

from xsdformer.protobuf import generator as proto_gen
from xsdformer.py import xml_converter
from xsdformer.transforms import TransformConfig, apply_transforms
from xsdformer.xsd import xsd


def main() -> None:
    xsd_path = pathlib.Path(sys.argv[1])
    xml_path = pathlib.Path(sys.argv[2])
    max_records = int(sys.argv[3]) if len(sys.argv) > 3 else 3

    transforms_path = pathlib.Path(__file__).parent.parent / 'clinvar_transforms.yaml'
    config = TransformConfig.from_yaml(transforms_path)

    type_defs = xsd.process_xsd(str(xsd_path))
    type_defs = apply_transforms(type_defs, config)

    namespace = 'clinvar'

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)

        proto_def = '\n'.join(proto_gen.generate(namespace, type_defs))
        proto_path = tmp_path / f'{namespace}.proto'
        proto_path.write_text(proto_def)

        spec = importlib.util.find_spec('google.protobuf.timestamp_pb2')
        proto_include = pathlib.Path(spec.origin).parent.parent

        subprocess.run(  # noqa: S603
            [
                sys.executable,
                '-m',
                'grpc_tools.protoc',
                f'--proto_path={tmp_path}',
                f'--proto_path={proto_include}',
                f'--python_out={tmp_path}',
                str(proto_path.relative_to(tmp_path)),
            ],
            check=True,
        )

        pb2_spec = importlib.util.spec_from_file_location(
            f'{namespace}_pb2',
            tmp_path / f'{namespace}_pb2.py',
        )
        module_pb2 = importlib.util.module_from_spec(pb2_spec)
        pb2_spec.loader.exec_module(module_pb2)

        sys.modules[module_pb2.__name__] = module_pb2
        converter_code = '\n'.join(
            xml_converter.generate(namespace, type_defs, module_pb2.__name__),
        )
        converter = types.ModuleType('clinvar_converter')
        exec(converter_code, converter.__dict__)  # noqa: S102

        # Stream XML — ClinVar files are huge, use iterparse.
        open_fn = gzip.open if xml_path.suffix == '.gz' else open
        count = 0
        with open_fn(str(xml_path), 'rb') as f:
            for _event, elem in etree.iterparse(f, events=('end',), tag='VariationArchive'):
                try:
                    proto = converter.VariationArchiveType(elem)
                    vid = proto.variation_id
                    print(f'# VCV: {proto.accession} (VariationID={vid})')
                    print(text_format.MessageToString(proto))
                except Exception as e:  # noqa: BLE001
                    print(f'# ERROR on VariationID={elem.get("VariationID", "?")}: {e}', file=sys.stderr)
                elem.clear()
                count += 1
                if count >= max_records:
                    break


if __name__ == '__main__':
    main()
