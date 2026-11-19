"""Try parameter values on songs whose model outputs are cached, and score each.

    python -m kizami.sweep SONG_FOLDER... --model-dir M --cache C --param lag_en=-0.06:0.0:0.01

Each song folder holds ``vocals.wav``, ``lyrics.txt`` and ``ref.json`` (a
reference made by ``kizami import-ref`` or the tagger). With ``--cache`` the
models do not run again, so one value takes a few seconds per song. Only
counts and scores are printed, never lyric text.
"""

from __future__ import annotations

import argparse
import itertools
import math
import tempfile
from pathlib import Path


def parse_param(text: str) -> tuple[str, list[float]]:
    """``name=start:stop:step`` (stop included), ``name=a,b,c`` or ``name=value``."""
    from kizami.generate.place import DEFAULTS

    name, _, spec = text.partition("=")
    name = name.strip()
    if name not in DEFAULTS:
        raise ValueError(f"unknown parameter: {name}")
    if ":" in spec:
        start, stop, step = (float(x) for x in spec.split(":"))
        if step <= 0:
            raise ValueError("the step must be above 0")
        n = int(math.floor((stop - start) / step + 1e-9))  # never past stop
        return name, [round(start + i * step, 6) for i in range(n + 1)]
    return name, [float(x) for x in spec.split(",") if x.strip()]


def main(argv: list[str] | None = None) -> None:
    from kizami.evaluate import combine_results, evaluate_pair
    from kizami.generate import generate
    from kizami.timing import load_document

    ap = argparse.ArgumentParser(prog="python -m kizami.sweep", description=__doc__.splitlines()[0])
    ap.add_argument("songs", nargs="+", help="song folders with vocals.wav, lyrics.txt and ref.json")
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--cache", required=True, help="folder with the cached model outputs")
    ap.add_argument("--param", action="append", default=[], help="name=start:stop:step, name=a,b or name=value")
    ap.add_argument("--level", default="chu", choices=["dai", "chu", "sho"])
    ap.add_argument("--anchor", default="prev-end", choices=["prev-end", "attack"])
    ap.add_argument("--latin", default="auto", choices=["auto", "en", "es"])
    ap.add_argument("--tolerance", type=int, default=50, help="ms")
    args = ap.parse_args(argv)

    grid = [parse_param(p) for p in args.param]
    names = [n for n, _ in grid]
    print("\t".join([*names, "recall", "precision", "hits", "ref", "gen"]))
    for values in itertools.product(*[v for _, v in grid]) if grid else [()]:
        params = dict(zip(names, values), anchor=args.anchor)
        results = []
        for song in args.songs:
            with tempfile.TemporaryDirectory() as out:
                generate(song, model_dir=args.model_dir, out_dir=out, levels=[args.level], kanji="keep",
                         cache=args.cache, params=params, latin=args.latin)  # fmt: skip
                hyp = load_document(Path(out) / f"auto_{args.level}.json")
            results.append(evaluate_pair(load_document(Path(song) / "ref.json"), hyp, args.tolerance))
        r = combine_results(results)
        print("\t".join([*(f"{v:g}" for v in values), f"{r.recall:.4f}", f"{r.precision:.4f}",
                         str(r.hit_count), str(r.ref_count), str(r.hyp_count)]))  # fmt: skip


if __name__ == "__main__":
    main()
