# Kizami

Kizami makes timed lyrics for Japanese songs, for karaoke-style lyric
players. Each piece of text lights up at the moment the singer starts to
sing it, so the lyrics move with the rhythm of the voice, like a rhythm game.

> **Status: early development.** `kizami generate` makes chunk timing from a
> separated vocal track and a lyrics file, and `kizami tagger` lets you
> correct it. Export to lyric-player formats (TTML, LRC) is not built yet.

## What makes it different

- **Timing follows the voice.** Text changes at the vocal attack, not at
  grammar boundaries.
- **Three chunk sizes.** Words with their particles (`夜空を`), words plus
  the strong attacks inside them, or every character (`夜`, `空`, `を`). The
  same character starts at the same time in all three.
- **Everything runs on your computer.** You give Kizami a song file and a
  lyrics text file that you already have. Kizami does not download, store,
  or share lyrics or audio.

## Planned for the first release

- Input: a lyrics text file and a normal mixed song file.
- Output: TTML for lyric players such as AMLL. LRC and ASS on request.
- Singer parts for duets, from tags in the lyrics file.
- Japanese first. Korean is planned for later.

## What you can do today

Measure how close a timing file is to a hand-made reference.

1. Install (Python 3.10 or newer):

   ```
   git clone https://github.com/Kei-Takamizawa/Kizami.git
   cd Kizami
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e .
   ```

2. Make a reference in Audacity and convert it. See the
   [labelling guide](docs/labeling-guide.md).

   ```
   kizami import-labels labels.txt -o ref.json
   ```

3. Compare a generated timing file with the reference:

   ```
   kizami eval ref.json generated.json --details
   ```

   The report shows two scores. **Recall** is the share of reference chunk
   starts that the generated file matches within 50 ms. **Precision** is the
   share of generated chunk starts that are correct. The targets are 85% and
   80%.

## Generate timing

`kizami generate` reads a song folder that holds `vocals.wav` (the vocal
track, separated from the mix beforehand) and `lyrics.txt`, and writes the
timing at three levels next to them:

| Level | Files | One chunk is |
|---|---|---|
| `dai` | `auto_dai.json`, `auto_labels_dai.txt` | a word with its particles |
| `chu` | `auto_chu.json`, `auto_labels_chu.txt` | `dai` plus the strongest attacks inside words |
| `sho` | `auto_sho.json`, `auto_labels_sho.txt` | every character with a sound of its own |

```
pip install -e ".[generate]"
kizami generate path/to/song-folder --model-dir path/to/models
```

It needs the `ffmpeg` program and about 2.5 GB of model files, which you
download once into the model folder (or set `KIZAMI_MODEL_DIR` to it). A song
takes about 40 seconds on an Apple silicon Mac. Open the label files in
`kizami tagger` or Audacity to review them; a `?` before the text means no
clear attack was found for that chunk. See [Generating timing](docs/generate.md)
for the model setup and how a chunk start is chosen.

## Tag timing by tapping

`kizami tagger` opens a window in your browser where you tag lyric timing the
way you play a rhythm game: the song plays, and you press a key each time the
next piece of the lyrics is sung.

```
kizami tagger path/to/song-folder
```

The song folder holds the audio (for example `vocals.wav`) and `lyrics.txt`.
You can slow the song down, drag the start and end of every chunk on the
waveform, and start from an existing label file instead of from nothing. The
result is saved in the same folder as `tagger_labels.txt`, which
`kizami import-labels` reads. Everything stays on your computer. See the
[tagger guide](docs/tagger.md).

## Documents

- [Design](docs/design.md): goals, definitions, evaluation method, planned
  pipeline.
- [Generating timing](docs/generate.md): model setup, options, how a chunk
  start is chosen, measured accuracy.
- [Timing JSON format](docs/timing-json-format.md)
- [Labelling guide](docs/labeling-guide.md)
- [Tagger guide](docs/tagger.md)

## Development

```
pip install -e ".[dev]"
pytest
```

## Licence

MIT. The licence file is added before the first release.
