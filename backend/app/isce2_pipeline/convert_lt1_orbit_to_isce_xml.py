#!/usr/bin/env python3
"""Convert LT-1 text precise orbit files to the XML structure expected by ISCE2 LUTAN1."""

from __future__ import annotations

import argparse
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional


@dataclass
class StateVector:
    time: datetime
    x: float
    y: float
    z: float
    vx: float
    vy: float
    vz: float


def _guess_text_encoding(input_txt: Path) -> str:
    with input_txt.open("rb") as handle:
        sample = handle.read(4096)

    if sample.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if sample.startswith(b"\xfe\xff"):
        return "utf-16-be"

    # Handle UTF-16-style wide-char text without BOM.
    if sample and sample.count(b"\x00") * 4 >= len(sample):
        even_nuls = sample[0::2].count(b"\x00")
        odd_nuls = sample[1::2].count(b"\x00")
        return "utf-16-le" if odd_nuls >= even_nuls else "utf-16-be"

    return "utf-8"


def _preview_line(value: str, limit: int = 200) -> str:
    text = value.replace("\x00", "").replace("\t", " ").strip()
    if len(text) > limit:
        text = text[:limit] + "..."
    return text or "<empty>"


def _parse_error(input_txt: Path, line_no: int, message: str, raw_line: str = "") -> ValueError:
    detail = f"{message} (file={input_txt}, line={line_no})"
    if raw_line:
        detail += f". snippet={_preview_line(raw_line)!r}"
    return ValueError(detail)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert LT-1 GpsData .txt orbit files to ISCE2 LUTAN1 orbit XML."
    )
    parser.add_argument("input_txt", type=Path, help="Input LT-1 GpsData text file")
    parser.add_argument("output_xml", type=Path, help="Output orbit XML file")
    parser.add_argument(
        "--annotation-xml",
        type=Path,
        default=None,
        help="Optional LT-1 annotation/meta XML used to clip the orbit to scene time +/- margin",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="Optional explicit UTC start time, e.g. 2025-01-12T09:13:24.000000",
    )
    parser.add_argument(
        "--stop",
        type=str,
        default=None,
        help="Optional explicit UTC stop time, e.g. 2025-01-12T09:13:32.000000",
    )
    parser.add_argument(
        "--margin-sec",
        type=float,
        default=60.0,
        help="Seconds to expand around annotation/start-stop window",
    )
    return parser.parse_args()


def parse_flexible_datetime(value: str) -> datetime:
    value = value.strip().replace("Z", "")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        pass

    # LT-1 annotation files may store single-digit hours like T9:13:24.042863.
    match = re.match(r"^(\d{4}-\d{2}-\d{2}T)(\d{1})(:.*)$", value)
    if match:
        value = f"{match.group(1)}0{match.group(2)}{match.group(3)}"

    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    raise ValueError(f"Unsupported datetime format: {value}")


def parse_annotation_window(annotation_xml: Path, margin_sec: float) -> tuple[datetime, datetime]:
    root = ET.parse(annotation_xml).getroot()

    start_text = find_text(root, "productInfo/sceneInfo/start/timeUTC")
    stop_text = find_text(root, "productInfo/sceneInfo/stop/timeUTC")

    start_time = parse_flexible_datetime(start_text) - timedelta(seconds=margin_sec)
    stop_time = parse_flexible_datetime(stop_text) + timedelta(seconds=margin_sec)
    return start_time, stop_time


def find_text(root: ET.Element, path: str) -> str:
    node = root.find(path)
    if node is None or node.text is None:
        raise ValueError(f"Missing XML path: {path}")
    return node.text.strip()


def parse_orbit_file(input_txt: Path) -> list[StateVector]:
    vectors: list[StateVector] = []

    encoding = _guess_text_encoding(input_txt)
    with input_txt.open("rb") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.decode(encoding, errors="replace").replace("\ufeff", "")
            if not line.strip() or line.startswith("#"):
                continue

            if "\x00" in line:
                nul_count = line.count("\x00")
                raise _parse_error(
                    input_txt,
                    line_no,
                    (
                        "Orbit TXT contains NUL bytes and appears truncated or corrupted"
                        f" (nul_count={nul_count})"
                    ),
                    raw_line=line,
                )

            parts = line.split()
            if len(parts) < 12:
                raise _parse_error(
                    input_txt,
                    line_no,
                    f"Malformed orbit record: expected at least 12 columns, got {len(parts)}",
                    raw_line=line,
                )

            try:
                sec_float = float(parts[5])
                sec_int = int(sec_float)
                microsecond = int(round((sec_float - sec_int) * 1_000_000))

                timestamp = datetime(
                    int(parts[0]),
                    int(parts[1]),
                    int(parts[2]),
                    int(parts[3]),
                    int(parts[4]),
                    sec_int,
                    microsecond,
                )

                vectors.append(
                    StateVector(
                        time=timestamp,
                        x=float(parts[6]),
                        y=float(parts[7]),
                        z=float(parts[8]),
                        vx=float(parts[9]),
                        vy=float(parts[10]),
                        vz=float(parts[11]),
                    )
                )
            except ValueError as exc:
                raise _parse_error(
                    input_txt,
                    line_no,
                    f"Malformed orbit record: {exc}",
                    raw_line=line,
                ) from exc

    if not vectors:
        raise ValueError(f"No orbit records parsed from {input_txt}")

    return vectors


def clip_vectors(
    vectors: Iterable[StateVector],
    start_time: Optional[datetime],
    stop_time: Optional[datetime],
) -> list[StateVector]:
    if start_time is None and stop_time is None:
        return list(vectors)

    clipped = [
        vector
        for vector in vectors
        if (start_time is None or vector.time >= start_time)
        and (stop_time is None or vector.time <= stop_time)
    ]
    if not clipped:
        raise ValueError("No orbit records left after time clipping")
    return clipped


def build_xml(vectors: Iterable[StateVector]) -> ET.ElementTree:
    root = ET.Element("Earth_Explorer_File")
    data_block = ET.SubElement(root, "Data_Block")
    vectors = list(vectors)
    list_of_osvs = ET.SubElement(data_block, "List_of_OSVs", count=str(len(vectors)))

    for vector in vectors:
        osv = ET.SubElement(list_of_osvs, "OSV")
        ET.SubElement(osv, "UTC").text = vector.time.strftime("%Y-%m-%dT%H:%M:%S.%f")
        ET.SubElement(osv, "X").text = format_float(vector.x)
        ET.SubElement(osv, "Y").text = format_float(vector.y)
        ET.SubElement(osv, "Z").text = format_float(vector.z)
        ET.SubElement(osv, "VX").text = format_float(vector.vx)
        ET.SubElement(osv, "VY").text = format_float(vector.vy)
        ET.SubElement(osv, "VZ").text = format_float(vector.vz)

    ET.indent(root, space="  ")
    return ET.ElementTree(root)


def format_float(value: float) -> str:
    return f"{value:.10f}".rstrip("0").rstrip(".")


def main() -> None:
    args = parse_args()

    vectors = parse_orbit_file(args.input_txt)

    start_time: Optional[datetime] = None
    stop_time: Optional[datetime] = None

    if args.annotation_xml is not None:
        start_time, stop_time = parse_annotation_window(args.annotation_xml, args.margin_sec)

    if args.start:
        start_time = parse_flexible_datetime(args.start)
    if args.stop:
        stop_time = parse_flexible_datetime(args.stop)

    clipped = clip_vectors(vectors, start_time, stop_time)
    tree = build_xml(clipped)

    args.output_xml.parent.mkdir(parents=True, exist_ok=True)
    tree.write(args.output_xml, encoding="utf-8", xml_declaration=True)

    print(f"Input orbit:  {args.input_txt}")
    print(f"Output xml:   {args.output_xml}")
    print(f"Records read: {len(vectors)}")
    print(f"Records kept: {len(clipped)}")
    if start_time and stop_time:
        print(
            "Window:       "
            f"{start_time.strftime('%Y-%m-%dT%H:%M:%S.%f')} -> "
            f"{stop_time.strftime('%Y-%m-%dT%H:%M:%S.%f')}"
        )


if __name__ == "__main__":
    main()
