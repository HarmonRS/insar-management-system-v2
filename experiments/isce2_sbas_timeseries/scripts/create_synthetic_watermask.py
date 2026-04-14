#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a synthetic stripmapStack water mask in radar coordinates. "
            "The default fill value 1 means all-land, which preserves downstream pixels."
        )
    )
    parser.add_argument(
        "--like-image",
        required=True,
        help="Existing ISCE image base path or .xml path used only for shape/metadata, for example shadowMask.rdr",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output water-mask base path, for example .../geom_reference/waterMask.rdr",
    )
    parser.add_argument(
        "--fill-value",
        type=int,
        default=1,
        choices=(0, 1),
        help="Pixel value to write. 1 keeps all pixels, 0 masks all pixels.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing output mask.",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Optional JSON report path.",
    )
    return parser.parse_args()


def resolve_like_paths(value: str) -> tuple[Path, Path]:
    candidate = Path(value)
    if candidate.suffix == ".xml":
        xml_path = candidate
        image_path = Path(str(candidate)[:-4])
    else:
        image_path = candidate
        xml_path = Path(str(candidate) + ".xml")

    if not xml_path.exists():
        raise FileNotFoundError(f"Template image XML not found: {xml_path}")
    return image_path, xml_path


def maybe_unlink(path: Path) -> None:
    if path.exists():
        path.unlink()


def require_xml_value(root: ET.Element, property_name: str) -> str:
    value_node = root.find(f"./property[@name='{property_name}']/value")
    if value_node is None or value_node.text is None:
        raise ValueError(f"Missing XML property '{property_name}'")
    return value_node.text.strip()


def write_template_metadata(template_image: Path, template_xml: Path, output: Path) -> tuple[int, int]:
    root = ET.parse(template_xml).getroot()
    width = int(require_xml_value(root, "width"))
    length = int(require_xml_value(root, "length"))

    file_name_node = root.find("./property[@name='file_name']/value")
    if file_name_node is None:
        raise ValueError(f"Missing XML file_name entry: {template_xml}")
    file_name_node.text = str(output)

    xml_output = Path(str(output) + ".xml")
    ET.indent(root, space="    ")
    ET.ElementTree(root).write(xml_output, encoding="utf-8")

    hdr_template = template_image.with_suffix(".hdr")
    hdr_output = output.with_suffix(".hdr")
    if hdr_template.exists():
        hdr_text = hdr_template.read_text(encoding="utf-8", errors="ignore")
        hdr_output.write_text(hdr_text.replace(str(template_image), str(output)), encoding="utf-8")

    vrt_template = Path(str(template_image) + ".vrt")
    vrt_output = Path(str(output) + ".vrt")
    if vrt_template.exists():
        vrt_text = vrt_template.read_text(encoding="utf-8", errors="ignore")
        vrt_text = vrt_text.replace(template_image.name, output.name)
        vrt_output.write_text(vrt_text, encoding="utf-8")

    return width, length


def main() -> int:
    args = parse_args()
    template_image, template_xml = resolve_like_paths(args.like_image)
    output = Path(args.output)

    if output.exists() and not args.force:
        raise FileExistsError(f"Output already exists, use --force to overwrite: {output}")

    output.parent.mkdir(parents=True, exist_ok=True)
    width, length = write_template_metadata(template_image=template_image, template_xml=template_xml, output=output)

    mask = np.full((length, width), args.fill_value, dtype=np.uint8)
    mask.tofile(output)

    maybe_unlink(output.with_suffix(".rdr.aux.xml"))

    report = {
        "generated_at_utc": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "template_xml": str(template_xml),
        "output": str(output),
        "width": width,
        "length": length,
        "fill_value": args.fill_value,
        "data_type": "BYTE",
        "note": "Synthetic all-land water mask for local stripmapStack experiments without Earthdata SWBD access.",
    }

    report_path = Path(args.report) if args.report else output.parent / "synthetic_watermask_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Template: {template_xml}")
    print(f"Output:   {output}")
    print(f"Shape:    {length} x {width}")
    print(f"Value:    {args.fill_value}")
    print(f"Report:   {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
