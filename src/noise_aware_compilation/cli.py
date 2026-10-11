"""Command-line interface: ``nac convert``, ``nac place`` and ``nac example`` (v2 Part D).

Each command calls the function of the same name in
:mod:`noise_aware_compilation.api`, so the command line and the Python API
give identical results (R-I5). Invalid input exits with code 1 and a one-line
message on stderr; ``--debug`` shows the traceback instead (R-I7).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from noise_aware_compilation import __version__
from noise_aware_compilation.api import (
    InputError,
    convert_device,
    place_circuit,
    write_example,
)
from noise_aware_compilation.devices import available_devices
from noise_aware_compilation.placement import NoValidPlacementError
from noise_aware_compilation.scoring import DEFAULT_SCORER, scorer_names

PROG = "nac"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Noise-aware SWAP-free qubit placement for QEC circuits, built on Nove. "
            "Convert a device's calibration files, then place a Stim circuit on it. "
            "No Qiskit, decoder or simulation is needed."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--debug", action="store_true", help="show a full traceback when an error occurs"
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    convert = commands.add_parser(
        "convert",
        help="convert an IBM fake-backend file pair into a stored device",
        description=(
            "Convert an IBM fake-backend file pair (conf_*.json, props_*.json) into a stored "
            "converted device: a Nove CalibrationSnapshot and DeviceModel as JSON, plus "
            "hashes.json with their SHA-256 hashes."
        ),
    )
    convert.add_argument("--conf", required=True, help="backend configuration file (conf_*.json)")
    convert.add_argument("--props", required=True, help="backend properties file (props_*.json)")
    convert.add_argument("--out", required=True, help="directory to write the stored device to")
    convert.add_argument(
        "--name", help="name of the stored device (default: the backend name in the files)"
    )

    place = commands.add_parser(
        "place",
        help="choose a SWAP-free placement of a Stim circuit on a device",
        description=(
            "Choose a SWAP-free placement of a Stim circuit on a device from its calibrated "
            "noise. Writes mapping.json (chosen mapping, its score, the scorer, every candidate "
            "with its score, input file hashes, package version) and placed_circuit.stim (the "
            "relabelled circuit) to the output directory."
        ),
    )
    place.add_argument("--circuit", required=True, help="circuit as a Stim text file")
    place.add_argument(
        "--device",
        help=(
            "stored converted device (a directory written by 'nac convert') or the name of a "
            f"device shipped with the package ({', '.join(available_devices())}); "
            "use instead of --conf/--props"
        ),
    )
    place.add_argument(
        "--conf", help="backend configuration file (with --props, instead of --device)"
    )
    place.add_argument("--props", help="backend properties file (with --conf, instead of --device)")
    place.add_argument(
        "--scorer",
        choices=scorer_names(),
        default=DEFAULT_SCORER,
        help=f"selection score (default: {DEFAULT_SCORER})",
    )
    place.add_argument("--out", required=True, help="directory to write the results to")

    example = commands.add_parser(
        "example",
        help="write the shipped example circuit and device files",
        description=(
            "Write the example shipped with the package: a distance-3 bit-flip repetition code "
            "(repetition_d3_bitflip.stim) and FakeJakartaV2's IBM file pair (conf_jakarta.json, "
            "props_jakarta.json)."
        ),
    )
    example.add_argument("--out", required=True, help="directory to write the example files to")
    return parser


def _run(args: argparse.Namespace) -> int:
    if args.command == "convert":
        out = convert_device(args.conf, args.props, args.out, name=args.name)
        print(f"stored device {out.name!r} in {out.directory}")
        print(f"  snapshot sha256 {out.snapshot_sha256}")
        print(f"  device sha256   {out.device_sha256}")
        for note in out.notes:
            print(f"  note: {note}")
        return 0
    if args.command == "place":
        placed = place_circuit(
            args.circuit,
            args.out,
            device=args.device,
            conf=args.conf,
            props=args.props,
            scorer=args.scorer,
        )
        result = placed.result
        print(f"mapping (circuit qubit -> physical qubit): {json.dumps(placed.record['mapping'])}")
        count = len(result.candidates)
        print(f"score {result.score:.6g} ({result.scorer} scorer, {count} candidates)")
        print(f"wrote {placed.mapping_file} and {placed.circuit_file}")
        return 0
    if args.command == "example":
        for path in write_example(args.out):
            print(f"wrote {path}")
        return 0
    raise AssertionError(f"unhandled command {args.command!r}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except (InputError, NoValidPlacementError) as exc:
        if args.debug:
            raise
        message = str(exc)
    except Exception as exc:  # any other failure still gets one line unless --debug
        if args.debug:
            raise
        message = f"{type(exc).__name__}: {exc}"
    first_line = message.strip().splitlines()[0] if message.strip() else "unknown error"
    print(f"{PROG}: error: {first_line}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
