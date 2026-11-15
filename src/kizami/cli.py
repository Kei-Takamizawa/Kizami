"""Command line entry point: ``kizami <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from kizami.audacity import AudacityLabelError, load_labels
from kizami.evaluate import (
    DEFAULT_TARGET_PRECISION,
    DEFAULT_TARGET_RECALL,
    DEFAULT_TOLERANCE_MS,
    EvaluationError,
    combine_results,
    evaluate_pair,
    format_report,
    result_to_dict,
)
from kizami.timing import TimingFormatError, load_document, save_document


class CliError(Exception):
    """A problem to show to the user without a traceback."""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kizami",
        description="Onset-synced timed lyrics. This version has the evaluation tools only.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    import_labels = commands.add_parser(
        "import-labels",
        help="convert an Audacity label file into a reference timing file",
    )
    import_labels.add_argument("labels", help="label file exported from Audacity (.txt)")
    import_labels.add_argument("-o", "--output", required=True, help="timing JSON file to write")
    import_labels.add_argument("--title", help="song title to store in the file")

    evaluate = commands.add_parser(
        "eval",
        help="compare generated timing files against reference timing files",
    )
    evaluate.add_argument(
        "files",
        nargs="+",
        metavar="REFERENCE GENERATED",
        help="timing JSON files in pairs: reference first, then generated",
    )
    evaluate.add_argument(
        "--tolerance-ms",
        type=int,
        default=DEFAULT_TOLERANCE_MS,
        help=f"largest time difference that counts as a hit (default {DEFAULT_TOLERANCE_MS})",
    )
    evaluate.add_argument(
        "--target-recall",
        type=float,
        default=DEFAULT_TARGET_RECALL,
        help=f"pass mark for recall (default {DEFAULT_TARGET_RECALL})",
    )
    evaluate.add_argument(
        "--target-precision",
        type=float,
        default=DEFAULT_TARGET_PRECISION,
        help=f"pass mark for precision (default {DEFAULT_TARGET_PRECISION})",
    )
    evaluate.add_argument(
        "--details", action="store_true", help="list every boundary that is not a hit"
    )
    evaluate.add_argument("--json", action="store_true", help="print the results as JSON")
    return parser


def _run_import_labels(args: argparse.Namespace) -> None:
    meta = {"source": "audacity-labels"}
    if args.title:
        meta["title"] = args.title
    try:
        doc = load_labels(args.labels, meta)
    except (OSError, UnicodeDecodeError, AudacityLabelError) as exc:
        raise CliError(f"{args.labels}: {exc}") from exc

    save_document(doc, args.output)
    chunks = doc.lines[0].chunks
    uncertain = sum(1 for chunk in chunks if chunk.uncertain)
    print(f"Wrote {len(chunks)} chunks ({uncertain} marked uncertain) to {args.output}")


def _load(path: str):
    try:
        return load_document(path)
    except TimingFormatError as exc:
        raise CliError(str(exc)) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise CliError(f"{path}: {exc}") from exc


def _run_eval(args: argparse.Namespace) -> None:
    if len(args.files) % 2:
        raise CliError("give the files in pairs: REFERENCE GENERATED [REFERENCE GENERATED ...]")
    if args.tolerance_ms < 0:
        raise CliError("--tolerance-ms must be 0 or more")

    pairs = list(zip(args.files[0::2], args.files[1::2]))
    results = []
    for ref_path, hyp_path in pairs:
        ref = _load(ref_path)
        hyp = _load(hyp_path)
        try:
            results.append(evaluate_pair(ref, hyp, tolerance_ms=args.tolerance_ms))
        except EvaluationError as exc:
            raise CliError(f"{ref_path} against {hyp_path}: {exc}") from exc

    if args.json:
        payload = {
            "pairs": [
                {"reference": ref_path, "generated": hyp_path, **result_to_dict(result)}
                for (ref_path, hyp_path), result in zip(pairs, results)
            ],
            "total": result_to_dict(combine_results(results)),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    labelled = [
        (f"{ref_path} against {hyp_path}", result)
        for (ref_path, hyp_path), result in zip(pairs, results)
    ]
    print(
        format_report(
            labelled,
            target_recall=args.target_recall,
            target_precision=args.target_precision,
            details=args.details,
        ),
        end="",
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "import-labels":
            _run_import_labels(args)
        else:
            _run_eval(args)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
