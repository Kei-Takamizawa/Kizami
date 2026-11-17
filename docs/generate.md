# Generating timing

`kizami generate` turns a vocal track and its lyrics into chunk timing at
three levels. Everything runs on your computer; nothing is downloaded while it
runs.

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
| `--level` | `all` | `dai`, `chu`, `sho` or `all`. |
| `-o`, `--out-dir` | the song folder | Where the files are written. Existing files of the same name are replaced. |
| `--mms` | `auto` | Second model: `auto` uses it if `mms_fa/model.pt` is there, `on` requires it, `off` skips it. |
| `--cache` | none | Folder for the model outputs. A rerun on the same audio then takes a few seconds. |
| `--anchor` | `prev-end` | Chunk start at the end of the previous sound (`prev-end`) or at the attack itself (`attack`). |

## Output

For each level, a timing file (`auto_<level>.json`, see the
[timing JSON format](timing-json-format.md)) and an Audacity label file
(`auto_labels_<level>.txt`) that `kizami tagger` and Audacity open. A chunk
with no clear attack of its own is `uncertain` in the JSON and starts with
`?` in the label file. The command prints counts only, never lyric text.

| Level | One chunk is |
|---|---|
| `dai` | a word with its particles, such as `夜空を` |
| `chu` | `dai`, plus the strongest 45% of the attacks inside words |
| `sho` | every character with a sound of its own; a kanji compound is split per kanji, and small kana, `っ`, `ー` and a drawn-out `う` join the character before |

The same character has the same start time at every level.

Each chunk with two or more morae also lists when every mora starts
(`morae` in the JSON, not in the label files). This gives times inside a kanji
that is never split, such as the が of `願`: the mora's aligned time snaps to
an attack between the previous mora and the next unit, with the same
start rule as a chunk. In a check on 1,075 pairs of neighbouring kana in the
four songs, timed this way as if they were one unit, the second kana was
within 50 ms of the owner's time as often as when timed as its own unit
(91.8%). Times inside real kanji have no reference yet.

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
   the new sound rises.

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

## From Python

```python
from kizami.generate import generate

result = generate("songs/demo", model_dir="models", levels=["sho"], progress=print)
for level in result.levels:
    print(level.level, level.chunks, level.labels)
```

`generate` raises `kizami.generate.GenerateError` with a message for the user
when an input or a model file is missing.
