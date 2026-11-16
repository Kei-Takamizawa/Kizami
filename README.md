# Kizami

Kizami makes timed lyrics for Japanese songs, for karaoke-style lyric
players. Each piece of text lights up at the moment the singer starts to
sing it, so the lyrics move with the rhythm of the voice, like a rhythm game.

> **Status: early development.** Kizami cannot generate timed lyrics yet.
> This version contains the tools that measure timing accuracy. The
> generator is built next and is tested with these tools.

## What makes it different

- **Timing follows the voice.** Text changes at the vocal attack, not at
  grammar boundaries.
- **Words stay whole.** A word such as `夜空` is never split in the middle.
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
