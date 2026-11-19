# Generating timing

`kizami generate` turns a vocal track and its lyrics into chunk timing. A
chunk starts at each word and at each strong vocal attack inside a word, and
kanji are written in hiragana so that a chunk can also start inside one (the
`が` of `願`). Everything runs on your computer; nothing is downloaded while
it runs.

```
kizami generate path/to/song-folder --model-dir path/to/models
```

## Input

The song folder holds two files (or name them with `--vocals` and `--lyrics`):

- `vocals.wav`: the vocal track alone, as a PCM WAV file. Separate it from
  the mix first, for example with Demucs. Use the same file for the tagger and
  for evaluation, so that all times share one time base.
- `lyrics.txt`: UTF-8 text, one lyric line per line. Blank lines and section
  tags such as `[Chorus]` are skipped. When a word is sung differently from
  how it is written, give the reading in braces: `明日{あした}` or
  `{明日|あした}`. The braces are not shown in the output.

## Models

Put the model files in one folder and pass it with `--model-dir`, or set the
environment variable `KIZAMI_MODEL_DIR` to it.

| Folder | Needed | What it is |
|---|---|---|
| `w2v2-hiragana/` | yes | `vumichien/wav2vec2-large-xlsr-japanese-hiragana` from Hugging Face: `config.json`, `model.safetensors`, `preprocessor_config.json`, `special_tokens_map.json`, `tokenizer_config.json`, `vocab.json` (about 1.3 GB). |
| `mms_fa/model.pt` | no | The torchaudio MMS_FA forced-alignment model from `https://dl.fbaipublicfiles.com/mms/torchaudio/ctc_alignment_mling_uroman/model.pt` (1,262,047,414 bytes). Check its licence before any commercial use. |

One way to fetch the first model:

```
mkdir -p models/w2v2-hiragana
cd models/w2v2-hiragana
for f in config.json model.safetensors preprocessor_config.json special_tokens_map.json tokenizer_config.json vocab.json; do
  curl -LO "https://huggingface.co/vumichien/wav2vec2-large-xlsr-japanese-hiragana/resolve/main/$f"
done
```

The `ffmpeg` program must be installed to read the audio for the models.

## Options

| Option | Default | Meaning |
|---|---|---|
| `--level` | `chu` | `chu` (the timing to use). `dai`, `sho` or `all` write the other two levels too, for comparison. |
| `--kanji` | `hiragana` | `hiragana` writes kanji as their reading, one unit per mora. `keep` writes them as in `lyrics.txt`; a kanji then never splits. |
| `-o`, `--out-dir` | the song folder | Where the files are written. Existing files of the same name are replaced. |
| `--mms` | `auto` | Second model: `auto` uses it if `mms_fa/model.pt` is there, `on` requires it, `off` skips it. |
| `--cache` | none | Folder for the model outputs. A rerun on the same audio then takes a few seconds. |
| `--anchor` | `prev-end` | Chunk start at the end of the previous sound (`prev-end`) or at the attack itself (`attack`). |

## Output

A timing file (`auto_chu.json`, see the
[timing JSON format](timing-json-format.md)) and an Audacity label file
(`auto_labels_chu.txt`). Open the timing file in the tagger to review it with
its lyric lines: `kizami tagger song-folder --labels auto_chu.json`. (The
label file has no line breaks, and with kanji in hiragana its text no longer
matches `lyrics.txt`, so the tagger would have to guess the lines.) A chunk
with no clear attack of its own is `uncertain` in the JSON and starts with
`?` in the label file. The command prints counts only, never lyric text.

| Level | One chunk is |
|---|---|
| `chu` | a word with its particles, plus the strongest 45% of the attacks inside words, plus any sound that comes 1 s or more after the one before it |
| `dai` | a word with its particles, such as `夜空を` (with `--level dai` or `all`) |
| `sho` | every unit with a sound of its own (with `--level sho` or `all`); small kana, `っ`, `ー` and a drawn-out `う` join the kana before |

The same character has the same start time at every level, and with
`--kanji hiragana` or `keep`. `kizami eval` compares files by their text, so
compare against a reference written with kanji using `--kanji keep`.

Each chunk with two or more morae also lists when every mora starts
(`morae` in the JSON, not in the label files), for players that highlight
inside a chunk.

## How a chunk start is chosen

1. **Units.** The lyrics are split into units with SudachiPy. A kanji gets
   its reading from the dictionary; a compound whose reading cannot be split
   per kanji (such as `今日`) stays one unit.
2. **Alignment.** A wav2vec2 model trained on hiragana finds where each kana
   is sung (CTC forced alignment). Where it is unsure and the optional MMS
   model points at a clearer attack more than 0.15 s away, the MMS time is
   used.
3. **Attack.** The aligned time snaps to the strongest nearby vocal attack
   (spectral flux, 70 ms before to 50 ms after). A unit with no attack nearby
   keeps the aligned time and is marked uncertain. A line's first unit can
   move to an earlier strong attack that begins the same sound, and a
   sibilant to where its hiss begins.
4. **Start.** The chunk starts where the previous sound falls into the last
   valley before the attack. With no valley (an unbroken note change) it
   starts 20 ms before the attack; after more than 0.3 s of silence, where
   the new sound rises. So the text appears a little before the attack
   (about 0.1 s on average), which is where the owner placed starts by ear.
5. **Morae inside a unit.** For a unit with two or more morae, such as `願`
   (ね, が), each later mora is placed like a unit: its aligned time snaps to
   an attack between the previous mora and the next unit, and it starts by
   the rule of step 4.
6. **Level chu.** A unit starts a chunk if it starts a word (particles stay
   with the word before) or if its attack is among the strongest 45% of the
   attacks inside words. With `--kanji hiragana` a unit with kanji is then
   split into its morae, and a later mora also starts a chunk when its attack
   is at least as strong as that 45% line. The line is set before the split,
   so every other chunk start is the same as with `--kanji keep`.
   A unit that starts 1 second or more after the unit before it in the same
   line (after a long note or a rest) also starts a chunk, with or without a
   strong attack (`gap` in the parameters; 0 turns it off).

The parameters are in `kizami.generate.place.DEFAULTS`.

## Measured accuracy

Measured on 2026-11-17 against full-song references that the owner corrected
in the tagger. Recall is the share of reference chunk starts matched within
50 ms. Songs 1 to 3 were used to tune the parameters; song 4 was not.

| | Song 1 | Song 2 | Song 3 | Song 4 |
|---|---|---|---|---|
| `sho` | 90.6% | 90.7% | 89.9% | 85.9% |
| `chu` | 87.2% | | | |
| `dai` | 93.0% | | | |

Known weak spots: characters without an attack of their own (about half are
within 50 ms), and fast runs where both models are a syllable late.

**Chunks inside a kanji** (`--kanji hiragana`, measured 2026-11-19). All
chunk starts outside kanji are the same as with `--kanji keep`, so the table
above still holds for them. The split adds 27, 27, 39 and 12 chunk starts
inside kanji to songs 1 to 4 (282 to 309 chunks for song 1). There is no
reference for times inside a kanji. As a stand-in, two neighbouring kana of
one word were timed as if they were one kanji with two morae: where the
second kana had an attack as strong as the chu line, it was within 50 ms of
the owner's time in 128 of 132 cases (97.0%, 95% interval 92.5 to 98.8%) over
the four songs. Searching wider for a strong attack when a mora has none
nearby was tried and not kept: the extra splits it found were right only half
the time.

**The 1-second rule** (measured 2026-11-19) adds 7, 8, 1 and 0 chunk starts
to songs 1 to 4; 4 of the 16 have no clear attack and are marked uncertain.
Of the 9 that start on a character with a reference time, 6 are within 50 ms.

## From Python

```python
from kizami.generate import generate

result = generate("songs/demo", model_dir="models", levels=["sho"], progress=print)
for level in result.levels:
    print(level.level, level.chunks, level.labels)
```

`generate` raises `kizami.generate.GenerateError` with a message for the user
when an input or a model file is missing.
