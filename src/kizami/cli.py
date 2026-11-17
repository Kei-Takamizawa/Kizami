"""Command line entry point: ``kizami <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from kizami.audacity import AudacityLabelError, label_text_from_document
from kizami.audacity import import_labels as import_label_file
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
from kizami.tagger import DEFAULT_OUTPUT as DEFAULT_TAGGER_OUTPUT
from kizami.tagger import DEFAULT_PORT as DEFAULT_TAGGER_PORT
from kizami.tagger import TaggerError, serve
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
    import_labels.add_argument(
        "--start", type=float, help="first second of the section that was labelled or reviewed"
    )
    import_labels.add_argument(
        "--end", type=float, help="last second of the section that was labelled or reviewed"
    )
    import_labels.add_argument(
        "--skip-empty",
        action="store_true",
        help="drop labels without text (unused candidates from suggest-labels)",
    )

    export_labels = commands.add_parser(
        "export-labels",
        help="write a timing file as Audacity labels, to review it in Audacity",
    )
    export_labels.add_argument("timing", help="timing JSON file")
    export_labels.add_argument("-o", "--output", required=True, help="label file to write (.txt)")

    suggest = commands.add_parser(
        "suggest-labels",
        help="find where sounds start in a vocal track and write them as empty labels",
    )
    suggest.add_argument("audio", help="vocal track as a PCM WAV file")
    suggest.add_argument("-o", "--output", required=True, help="label file to write (.txt)")
    suggest.add_argument("--start", type=float, help="first second of the section to label")
    suggest.add_argument("--end", type=float, help="last second of the section to label")
    suggest.add_argument(
        "--threshold-db", type=float, help="lower gives more candidates (default 2.0)"
    )
    suggest.add_argument(
        "--min-gap-ms", type=int, help="smallest time between two candidates (default 60)"
    )

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

    tagger = commands.add_parser(
        "tagger",
        help="open the tap-to-tag window for one song folder in the browser",
    )
    tagger.add_argument("folder", help="song folder with the audio file and lyrics.txt")
    tagger.add_argument(
        "--labels", help="label file in the folder to start from, for example auto_labels.txt"
    )
    tagger.add_argument(
        "--output",
        default=DEFAULT_TAGGER_OUTPUT,
        help=f"label file to write in the folder (default {DEFAULT_TAGGER_OUTPUT})",
    )
    tagger.add_argument(
        "--port", type=int, default=DEFAULT_TAGGER_PORT, help="port on this computer to use"
    )
    tagger.add_argument(
        "--no-browser", action="store_true", help="print the address but do not open the browser"
    )
    return parser


def _run_import_labels(args: argparse.Namespace) -> None:
    meta = {"source": "audacity-labels"}
    if args.title:
        meta["title"] = args.title
    if args.start is not None and args.start < 0:
        raise CliError("--start must be 0 or more")
    if args.start is not None and args.end is not None and args.end <= args.start:
        raise CliError("--end must be later than --start")
    start_ms = None if args.start is None else round(args.start * 1000)
    end_ms = None if args.end is None else round(args.end * 1000)
    if start_ms is not None or end_ms is not None:
        meta["range_ms"] = [start_ms, end_ms]
    try:
        imported = import_label_file(
            args.labels, meta, skip_empty=args.skip_empty, start_ms=start_ms, end_ms=end_ms
        )
    except (OSError, UnicodeDecodeError, AudacityLabelError) as exc:
        raise CliError(f"{args.labels}: {exc}") from exc

    save_document(imported.document, args.output)
    chunks = imported.document.lines[0].chunks
    uncertain = sum(1 for chunk in chunks if chunk.uncertain)
    print(f"Wrote {len(chunks)} chunks ({uncertain} marked uncertain) to {args.output}")
    if imported.skipped_empty:
        print(f"Dropped {imported.skipped_empty} labels without text")


def _run_export_labels(args: argparse.Namespace) -> None:
    doc = _load(args.timing)
    text = label_text_from_document(doc)
    with open(args.output, "w", encoding="utf-8", newline="\n") as out:
        out.write(text)
    print(f"Wrote {text.count(chr(10))} labels to {args.output}")


# Audio around the section is analysed too, so that a sound that starts at
# the edge of the section is seen together with what comes before it.
SECTION_MARGIN_S = 0.5


def _run_suggest_labels(args: argparse.Namespace) -> None:
    start_s = 0.0 if args.start is None else args.start
    if start_s < 0:
        raise CliError("--start must be 0 or more")
    if args.end is not None and args.end <= start_s:
        raise CliError("--end must be later than --start")

    try:
        from kizami import onsets
    except ImportError as exc:
        raise CliError(
            'this command needs numpy. Install it with: pip install "kizami[audio]"'
        ) from exc

    try:
        samples, rate = onsets.read_wav_mono(args.audio)
    except (OSError, onsets.OnsetError) as exc:
        raise CliError(f"{args.audio}: {exc}") from exc

    end_s = len(samples) / rate if args.end is None else args.end
    first = max(0, round((start_s - SECTION_MARGIN_S) * rate))
    last = min(len(samples), round((end_s + SECTION_MARGIN_S) * rate))
    options = {}
    if args.threshold_db is not None:
        options["threshold_db"] = args.threshold_db
    if args.min_gap_ms is not None:
        options["min_gap_ms"] = args.min_gap_ms
    offset_ms = round(1000 * first / rate)
    found = [ms + offset_ms for ms in onsets.detect_onsets(samples[first:last], rate, **options)]
    kept = [ms for ms in found if start_s * 1000 <= ms <= end_s * 1000]

    with open(args.output, "w", encoding="utf-8", newline="\n") as out:
        out.write(onsets.label_text_from_onsets(kept))
    print(f"Wrote {len(kept)} candidates to {args.output}")


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


def _run_tagger(args: argparse.Namespace) -> None:
    try:
        serve(
            args.folder,
            port=args.port,
            output=args.output,
            labels=args.labels,
            open_browser=not args.no_browser,
        )
    except TaggerError as exc:
        raise CliError(str(exc)) from exc


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "import-labels":
            _run_import_labels(args)
        elif args.command == "suggest-labels":
            _run_suggest_labels(args)
        elif args.command == "export-labels":
            _run_export_labels(args)
        elif args.command == "tagger":
            _run_tagger(args)
        else:
            _run_eval(args)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
