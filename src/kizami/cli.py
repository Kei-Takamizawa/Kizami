"""Command line entry point: ``kizami <command>``."""

from __future__ import annotations

import argparse
import json
import os
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
from kizami.app import DEFAULT_PORT as DEFAULT_APP_PORT
from kizami.app import DEFAULT_WORK_DIR as DEFAULT_APP_WORK_DIR
from kizami.tagger import DEFAULT_OUTPUT as DEFAULT_TAGGER_OUTPUT
from kizami.tagger import DEFAULT_PORT as DEFAULT_TAGGER_PORT
from kizami.tagger import TaggerError, serve
from kizami.timing import TimingFormatError, load_document, save_document


class CliError(Exception):
    """A problem to show to the user without a traceback."""


MODEL_DIR_ENV = "KIZAMI_MODEL_DIR"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kizami",
        description="Onset-synced timed lyrics: generate, review and evaluate chunk timing.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    gen = commands.add_parser(
        "generate",
        help="make chunk timing for a song from its vocal track and lyrics",
        description=(
            "Write auto_chu.json and auto_labels_chu.txt: chunk timing that starts a chunk at each word "
            "and at the strongest attacks inside words (also inside a kanji, which is then shown in hiragana). "
            "Prints counts only."
        ),
    )
    gen.add_argument("folder", nargs="?", help="song folder with vocals.wav and lyrics.txt")
    gen.add_argument("--vocals", help="vocal track (PCM WAV) if not FOLDER/vocals.wav")
    gen.add_argument("--lyrics", help="lyrics file (UTF-8) if not FOLDER/lyrics.txt")
    gen.add_argument("-o", "--out-dir", help="folder to write to (default: the song folder)")
    gen.add_argument(
        "--level",
        default="chu",
        choices=["chu", "dai", "sho", "all"],
        help="level to write: chu (words plus strong attacks, default); dai (words) and sho (every kana) for comparison",
    )
    gen.add_argument(
        "--kanji",
        default="auto",
        choices=["auto", "hiragana", "keep"],
        help="auto (default): a chunk can start inside a kanji, and only such kanji words are shown in hiragana; "
        "hiragana: show every kanji in hiragana; keep: show kanji as written, a kanji never splits",
    )
    gen.add_argument(
        "--latin",
        default="auto",
        choices=["auto", "en", "es"],
        help="language of lyrics in Latin letters: auto (default, guessed from the words), en or es",
    )
    gen.add_argument(
        "--model-dir",
        default=os.environ.get(MODEL_DIR_ENV),
        help=f"folder with w2v2-hiragana/ (Japanese) and mms_fa/model.pt (Korean, English, Spanish; "
        f"optional for Japanese) (default ${MODEL_DIR_ENV})",
    )
    gen.add_argument(
        "--mms",
        default="auto",
        choices=["auto", "on", "off"],
        help="second model for unsure characters; auto uses it if its file is there",
    )
    gen.add_argument("--cache", help="folder to keep model outputs in, so a rerun is fast")
    gen.add_argument(
        "--anchor",
        default="prev-end",
        choices=["prev-end", "attack"],
        help="chunk start: end of the previous sound (default) or the attack itself",
    )

    import_ref = commands.add_parser(
        "import-ref",
        help="make a reference timing file from a public dataset (JamendoLyrics, CSD)",
        description="Make a reference timing JSON from a public dataset, for kizami eval.",
    )
    import_ref.add_argument("times", help="times (JamendoLyrics: annotations/words/SONG.csv; CSD: csv/SONG.csv)")
    import_ref.add_argument(
        "--format", required=True, choices=["jamendo", "csd"], help="dataset: jamendo (JamendoLyrics) or csd (Korean CSD)"
    )
    import_ref.add_argument("--words", help="word list, JamendoLyrics only (lyrics/SONG.words.txt)")
    import_ref.add_argument("--lyrics", required=True, help="lyrics with line breaks (JamendoLyrics: lyrics/SONG.txt; CSD: lyric/SONG.txt)")
    import_ref.add_argument("-o", "--output", required=True, help="timing JSON file to write")

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

    ttml = commands.add_parser(
        "ttml",
        help="write a timing file or tagger project as TTML for lyric players such as AMLL",
        description=(
            "Write TTML with one span per chunk. A chunk ends where the voice stops (read from the vocal "
            "track) or where the next chunk starts."
        ),
    )
    ttml.add_argument("timing", help="timing file (for example auto_chu.json) or tagger project (tagger.json)")
    ttml.add_argument("-o", "--output", help="TTML file to write (default: the input name with .ttml)")
    ttml.add_argument("--no-chunk-spaces", action="store_true", help="no space after each chunk (spaces in the lyrics stay)")
    ttml.add_argument(
        "--audio",
        help="vocal track (PCM WAV) to find where the voice stops (default: vocals.wav next to the input, if it is there)",
    )
    ttml.add_argument("--no-audio", action="store_true", help="do not read audio; end chunks at the next start")
    ttml.add_argument(
        "--offset-ms",
        type=int,
        default=0,
        help="move every time by this many ms in the file only; positive shows the lyrics later (default 0)",
    )
    ttml.add_argument("--title", help="song title to store in the file")
    ttml.add_argument("--artist", action="append", default=[], help="artist to store in the file (repeat for more)")
    ttml.add_argument("--album", help="album to store in the file")
    ttml.add_argument("--lang", default=None, help="language code of the lyrics (default: the main language of the timing file, else ja)")

    app = commands.add_parser(
        "app",
        help="open the app in the browser: a song file and pasted lyrics in, a TTML file out",
    )
    app.add_argument(
        "--model-dir",
        default=os.environ.get(MODEL_DIR_ENV),
        help=f"folder with w2v2-hiragana/ and optionally mms_fa/model.pt (default ${MODEL_DIR_ENV})",
    )
    app.add_argument(
        "--work-dir",
        default=DEFAULT_APP_WORK_DIR,
        help=f"folder that keeps one folder per song (default ./{DEFAULT_APP_WORK_DIR})",
    )
    app.add_argument("--port", type=int, default=DEFAULT_APP_PORT, help="port on this computer to use")
    app.add_argument("--no-browser", action="store_true", help="print the address but do not open the browser")
    app.add_argument(
        "--make-launcher",
        nargs="?",
        const="",
        metavar="FILE",
        help="instead of starting the app, make a file that starts it when you double-click it "
        "(default ~/Desktop/Kizami.command; keeps --model-dir, --work-dir and --port)",
    )

    tagger = commands.add_parser(
        "tagger",
        help="open the tap-to-tag window for one song folder in the browser",
    )
    tagger.add_argument("folder", help="song folder with the audio file and lyrics.txt")
    tagger.add_argument(
        "--labels",
        help="label file (.txt) or timing file (.json) in the folder to start from, for example auto_chu.json",
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


def _run_import_ref(args: argparse.Namespace) -> None:
    from kizami.datasets import DatasetError, csd_reference, jamendo_reference

    if args.format == "jamendo" and not args.words:
        raise SystemExit("error: --words is needed for --format jamendo")
    try:
        if args.format == "csd":
            doc = csd_reference(args.times, args.lyrics)
        else:
            doc = jamendo_reference(args.times, args.words, args.lyrics)
    except (DatasetError, OSError, KeyError, ValueError) as exc:
        raise SystemExit(f"error: {exc}") from exc
    save_document(doc, args.output)
    print(f"Wrote {len(doc.lines)} lines, {sum(len(line.chunks) for line in doc.lines)} chunks to {args.output}")


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


def _run_generate(args: argparse.Namespace) -> None:
    from kizami.generate import LEVELS, GenerateError, generate

    if args.folder is None and (args.vocals is None or args.lyrics is None):
        raise CliError("give a song folder, or both --vocals and --lyrics")
    if not args.model_dir:
        raise CliError(f"give --model-dir or set {MODEL_DIR_ENV} to the folder that holds w2v2-hiragana/")
    try:
        result = generate(
            args.folder,
            model_dir=args.model_dir,
            vocals=args.vocals,
            lyrics=args.lyrics,
            out_dir=args.out_dir,
            levels=LEVELS if args.level == "all" else (args.level,),
            kanji=args.kanji,
            latin=args.latin,
            mms=args.mms,
            cache=args.cache,
            params={"anchor": args.anchor},
            progress=lambda message: print(f"{message} ...", file=sys.stderr, flush=True),
        )
    except GenerateError as exc:
        raise CliError(str(exc)) from exc

    second = f"{result.from_second_aligner} from the second model" if result.used_second_aligner else "second model off"
    print(
        f"{result.lines} lines, {result.units} units, {result.with_attack} on a clear attack, {second}, "
        f"{result.kanji_kept_whole} kanji compounds timed as one unit"
    )
    print("chunk start rule: " + ", ".join(f"{k} {v}" for k, v in result.start_rules.items()))
    if result.languages:
        names = ", ".join(f"{k} {v}" for k, v in result.languages.items())
        latin = f" (Latin letters read as {result.latin})" if result.languages.get(result.latin) else ""
        print(f"units per language: {names}{latin}")
    for lv in result.levels:
        shown = f", {lv.kanji_in_hiragana} kanji words in hiragana" if args.kanji == "auto" else ""
        print(f"{lv.level}: {lv.chunks} chunks ({lv.uncertain} uncertain{shown}) -> {lv.timing}, {lv.labels}")


def _run_ttml(args: argparse.Namespace) -> None:
    from pathlib import Path

    from kizami.ttml import TtmlError, load_for_export, render_ttml

    source = Path(args.timing)
    out = Path(args.output) if args.output else source.with_suffix(".ttml")
    if out.resolve() == source.resolve():
        raise CliError("the output must not be the input file")
    audio = None
    if not args.no_audio:
        audio = Path(args.audio) if args.audio else source.with_name("vocals.wav")
        if args.audio is None and not audio.is_file():
            audio = None
    try:
        doc = load_for_export(source)
        if audio is not None:
            try:
                from kizami.loudness import LoudnessError, add_end_times, loudness_from_wav
            except ImportError as exc:
                raise CliError('reading the audio needs numpy. Install it with: pip install "kizami[audio]", or use --no-audio') from exc
            try:
                doc = add_end_times(doc, loudness_from_wav(audio))
            except LoudnessError as exc:
                raise CliError(str(exc)) from exc
        result = render_ttml(
            doc,
            offset_ms=args.offset_ms,
            title=args.title,
            artists=args.artist,
            album=args.album,
            lang=args.lang,
            chunk_spaces=not args.no_chunk_spaces,
        )
    except TtmlError as exc:
        raise CliError(str(exc)) from exc
    out.write_text(result.text, encoding="utf-8")
    ends = f"ends from {audio.name}" if audio is not None else "ends at the next chunk (no audio)"
    print(f"Wrote {result.lines} lines, {result.chunks} chunks to {out} ({ends}, offset {args.offset_ms:+d} ms)")


def _run_app(args: argparse.Namespace) -> None:
    from kizami.app import DEFAULT_LAUNCHER, AppError, make_launcher
    from kizami.app import serve as serve_app

    if args.make_launcher is not None:
        if not args.model_dir:
            raise CliError(f"give --model-dir (or set {MODEL_DIR_ENV}) so that the launcher knows the model folder")
        try:
            path = make_launcher(args.make_launcher or DEFAULT_LAUNCHER, model_dir=args.model_dir,
                                 work_dir=args.work_dir, port=args.port)  # fmt: skip
        except AppError as exc:
            raise CliError(str(exc)) from exc
        print(f"Made {path}")
        print("Double-click it to open the Kizami app. For one click, drag it to the right end of the Dock.")
        return
    try:
        serve_app(work_dir=args.work_dir, model_dir=args.model_dir, port=args.port, open_browser=not args.no_browser)
    except AppError as exc:
        raise CliError(str(exc)) from exc


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
        if args.command == "generate":
            _run_generate(args)
        elif args.command == "import-labels":
            _run_import_labels(args)
        elif args.command == "import-ref":
            _run_import_ref(args)
        elif args.command == "suggest-labels":
            _run_suggest_labels(args)
        elif args.command == "export-labels":
            _run_export_labels(args)
        elif args.command == "tagger":
            _run_tagger(args)
        elif args.command == "ttml":
            _run_ttml(args)
        elif args.command == "app":
            _run_app(args)
        else:
            _run_eval(args)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
