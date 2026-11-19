# Kizami

Kizami makes timed lyrics for songs in Japanese, Korean, English and
Spanish, also when one song mixes them, for karaoke-style lyric players. Each piece of text lights up at the moment the singer starts to
sing it, so the lyrics move with the rhythm of the voice, like a rhythm game.

> **Status: early development.** The app makes a TTML file from a song file
> and its lyrics. Timing is measured on four test songs; see
> [Generating timing](docs/generate.md#measured-accuracy).

## What makes it different

- **Timing follows the voice.** Text changes at the vocal attack, not at
  grammar boundaries.
- **Chunks start on strong attacks.** A new chunk starts at each word and
  at each strong attack inside a word, so the text moves with the groove.
- **Four languages, also mixed.** Japanese, Korean, English and Spanish
  lyrics can be in one song and even in one line (K-pop with English, a
  Japanese version of a K-pop song). Korean is timed per syllable block,
  English and Spanish per syllable, Japanese per kana.
- **Chunks can start inside a kanji.** A chunk can start at a strong attack
  inside a kanji, such as the `が` of `願`. Kanji stay as written; only a
  word that is split this way is shown in hiragana (`ね` `がい`).
- **Everything runs on your computer.** You give Kizami a song file and a
  lyrics text file that you already have. Kizami does not download, store,
  or share lyrics or audio.

## Make a TTML file

`kizami app` opens a simple page in your browser. Choose a song file, paste
the lyrics, and press **Make TTML**. Kizami takes the voice out of the song,
times the lyrics, and gives you a TTML file for lyric players such as AMLL.
If a part is off, open it in the tagger from the same page, fix it by ear, and
download again.

```
pip install -e ".[app]"
kizami app --model-dir path/to/models
```

On a Mac, `kizami app --make-launcher --model-dir path/to/models` puts a
`Kizami.command` file on the Desktop: after that, a double-click (or one click
in the Dock) opens the app.

You need the `ffmpeg` program and the timing models, downloaded once: the
Japanese model (about 1.3 GB) for Japanese lyrics, and the letter model (about
1.3 GB) for Korean, English and Spanish. On an Apple silicon Mac, a song of about five minutes took 67 seconds
to separate the voice (once per song) and 40 seconds to time. You can choose
a space after each chunk and a display offset for players or earphones that
play the sound late. See
[the app guide](docs/app.md).

## Planned

- LRC and ASS output.
- Singer parts for duets, from tags in the lyrics file.
- Better accuracy for each language, measured on public data and on the
  owner's songs.

## Measure the timing

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
timing next to them as `auto_chu.json` and `auto_labels_chu.txt`. A chunk
starts at each word (with its particles), at the strongest attacks inside
words, and after a pause or long note of 1 second or more. Kanji stay as
written; a kanji word with a chunk starting inside it is written in hiragana
(`--kanji hiragana` writes all kanji in hiragana, `--kanji keep` never splits
a kanji).

```
pip install -e ".[generate]"
kizami generate path/to/song-folder --model-dir path/to/models
```

It needs the `ffmpeg` program and about 2.5 GB of model files, which you
download once into the model folder (or set `KIZAMI_MODEL_DIR` to it). A song
takes about 40 seconds on an Apple silicon Mac. To review it, open the
timing file in the tagger with `kizami tagger path/to/song-folder --labels
auto_chu.json`; a `?` before the text means no clear attack was found for
that chunk. See [Generating timing](docs/generate.md)
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

- [App guide](docs/app.md): song file and lyrics in, TTML out.
- [TTML output](docs/ttml.md): what the TTML file holds, where a chunk ends,
  `kizami ttml`.
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
