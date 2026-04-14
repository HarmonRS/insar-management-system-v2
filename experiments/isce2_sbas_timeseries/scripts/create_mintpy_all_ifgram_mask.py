#!/usr/bin/env python3
"""Create a strict MintPy mask containing only pixels valid in all interferograms."""

from __future__ import annotations

import argparse
from pathlib import Path

import h5py
import numpy as np


def build_mask(ifgram_stack: Path, output_path: Path, block_rows: int) -> None:
    with h5py.File(ifgram_stack, "r") as src:
        unwrap = src["unwrapPhase"]
        conn = src.get("connectComponent")

        num_ifg, length, width = unwrap.shape
        mask = np.ones((length, width), dtype=np.bool_)

        print(f"Input stack: {ifgram_stack}")
        print(f"Interferograms: {num_ifg}")
        print(f"Shape: {length} x {width}")
        print(f"Block rows: {block_rows}")

        for row0 in range(0, length, block_rows):
            row1 = min(row0 + block_rows, length)
            block = unwrap[:, row0:row1, :]
            block_mask = np.all(np.isfinite(block) & (block != 0.0), axis=0)

            if conn is not None:
                conn_block = conn[:, row0:row1, :]
                block_mask &= np.all(conn_block != 0, axis=0)

            mask[row0:row1, :] = block_mask
            print(f"Processed rows {row0}:{row1}")

        attrs = dict(src.attrs)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with h5py.File(output_path, "w") as dst:
        dst.create_dataset("mask", data=mask, dtype=np.bool_)
        for key, value in attrs.items():
            dst.attrs[key] = value
        dst.attrs["FILE_TYPE"] = "mask"
        dst.attrs["DATASET_NAME"] = "mask"
        dst.attrs["SOURCE_FILE"] = str(ifgram_stack)
        dst.attrs["MASK_RULE"] = "all_ifgrams_finite_nonzero_and_conncomp_nonzero"

    valid_pixels = int(mask.sum())
    total_pixels = int(mask.size)
    print(f"Output mask: {output_path}")
    print(f"Valid pixels: {valid_pixels}/{total_pixels} ({valid_pixels / total_pixels * 100:.2f}%)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a strict mask of pixels valid in all MintPy interferograms."
    )
    parser.add_argument("--ifgram-stack", required=True, help="Path to MintPy inputs/ifgramStack.h5")
    parser.add_argument("--output", required=True, help="Output HDF5 path, e.g. maskAllValid.h5")
    parser.add_argument(
        "--block-rows",
        type=int,
        default=256,
        help="Number of image rows processed per block.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    build_mask(
        ifgram_stack=Path(args.ifgram_stack),
        output_path=Path(args.output),
        block_rows=args.block_rows,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
