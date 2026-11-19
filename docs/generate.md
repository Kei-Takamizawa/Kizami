# Generating timing

`kizami generate` turns a vocal track and its lyrics into chunk timing. A
chunk starts at each word and at each strong vocal attack inside a word, also
inside a kanji (the `が` of `願`). Kanji stay as written; only a kanji word
with a chunk starting inside it is written in hiragana. Everything runs on your computer; nothing is downloaded while
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
  `{明日|あした}`. The braces are not shown in the output. The long form
  works in every language: `{24/7|twenty four seven}` is timed as one unit
  and shown as `24/7`.

## Languages

Lyrics can be in Japanese, Korean, English and Spanish, in any mix, also
inside one line (`Baby 사랑해`, `君のsmileが好き`). The script of each part
decides its language:

| Written in | Language | Smallest unit | A word is |
|---|---|---|---|
| kana, kanji | Japanese | a kana (or a kanji with its reading) | a word found by SudachiPy, with its particles |
| Hangul | Korean | one syllable block (`사`, `랑`) | the text between spaces (`사랑을`) |
| Latin letters | English or Spanish (`--latin`) | one syllable by the spelling rules (`beau`, `ti`, `ful`) | the text between spaces |

Latin letters are English or Spanish for the whole song: `--latin auto` (the
default) guesses from the words, `--latin en` or `--latin es` sets it. The
guess counts Spanish accents and common words of each language, each different
word once; fillers such as `oh`, `yeah` and `baby` count for neither. A song
with both English and Spanish is not supported. A Spanish word that starts
with a vowel after a word that ends with one (`mi amor`, sung `mia-mor`) does
not start a chunk of its own unless its vowel has a strong attack.

Japanese parts are timed by the Japanese model, all other parts by the letter
model. A line with Japanese only gets the same chunks as before, and the same
times, except one case: the first start of a line right after a line with
Latin text can move, because it starts where the sound of the line before
ends. Measured 2026-11-19 on the four Japanese test songs at the medium level:
2 of 1,052 starts on Japanese-only lines moved, and both moved closer to the
owner's times.

## Models

Put the model files in one folder and pass it with `--model-dir`, or set the
environment variable `KIZAMI_MODEL_DIR` to it.

| Folder | Needed | What it is |
|---|---|---|
| `w2v2-hiragana/` | for Japanese | `vumichien/wav2vec2-large-xlsr-japanese-hiragana` from Hugging Face: `config.json`, `model.safetensors`, `preprocessor_config.json`, `special_tokens_map.json`, `tokenizer_config.json`, `vocab.json` (about 1.3 GB). |
| `mms_fa/model.pt` | for Korean, English and Spanish; optional for Japanese (a second opinion) | The torchaudio MMS_FA forced-alignment model from `https://dl.fbaipublicfiles.com/mms/torchaudio/ctc_alignment_mling_uroman/model.pt` (1,262,047,414 bytes). Check its licence before any commercial use. |

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
| `--latin` | `auto` | Language of lyrics in Latin letters: `auto` (guessed from the words), `en` or `es`. |
| `--kanji` | `auto` | `auto` keeps kanji as written in `lyrics.txt`. Only a run of kanji with a chunk start inside the reading of one of them is written in hiragana: with a start at the `が` of `願い`, the chunks are `ね` and `がい`; with a start inside `空`, `夜空` becomes `よぞら` (not `夜そら`). A start between `夜` and `空` keeps `夜空`. `hiragana` writes every kanji as its reading. `keep` writes them as in `lyrics.txt`; a chunk then never starts inside a kanji. `auto` and `hiragana` give the same chunk starts. |
| `-o`, `--out-dir` | the song folder | Where the files are written. Existing files of the same name are replaced. |
| `--mms` | `auto` | Second model: `auto` uses it if `mms_fa/model.pt` is there, `on` requires it, `off` skips it. |
| `--cache` | none | Folder for the model outputs. A rerun on the same audio then takes a few seconds. |
| `--anchor` | `prev-end` | Chunk start at the end of the previous sound (`prev-end`) or at the attack itself (`attack`). |

## Output

A timing file (`auto_chu.json`, see the
[timing JSON format](timing-json-format.md)) and an Audacity label file
(`auto_labels_chu.txt`). Open the timing file in the tagger to review it with
its lyric lines: `kizami tagger song-folder --labels auto_chu.json`. (The
label file has no line breaks, and where kanji are written in hiragana its
text no longer matches `lyrics.txt`, so the tagger would have to guess the
lines.) A chunk
with no clear attack of its own is `uncertain` in the JSON and starts with
`?` in the label file. The command prints counts only, never lyric text; with
`--kanji auto` it also says how many kanji words were written in hiragana.

| Level | One chunk is |
|---|---|
| `chu` | a word with its particles, plus the strongest 45% of the attacks inside words, plus any sound that comes 1 s or more after the one before it |
| `dai` | a word with its particles, such as `夜空を` (with `--level dai` or `all`) |
| `sho` | every unit with a sound of its own (with `--level sho` or `all`); small kana, `っ`, `ー` and a drawn-out `う` join the kana before |

The same character has the same start time at every level and with every
`--kanji` mode. `kizami eval` compares files by their text, so
compare against a reference written with kanji using `--kanji keep`.

Each chunk with two or more morae also lists when every mora starts
(`morae` in the JSON, not in the label files), for players that highlight
inside a chunk.

## How a chunk start is chosen

1. **Units.** Japanese is split into units with SudachiPy. A kanji gets
   its reading from the dictionary; a compound whose reading cannot be split
   per kanji (such as `今日`) stays one unit. Korean, English and Spanish are
   split as in [Languages](#languages).
2. **Alignment.** A wav2vec2 model trained on hiragana finds where each kana
   is sung (CTC forced alignment). Where it is unsure and the optional MMS
   model points at a clearer attack more than 0.15 s away, the MMS time is
   used. Units in Korean, English and Spanish take their time from the MMS
   letter model (Korean is written in the letters MMS learned it with), plus
   a lag per language (`lag_en`, `lag_es`, `lag_ko`).
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
   attacks inside words. With `--kanji auto` or `hiragana` a unit with kanji
   is then split into its morae, and a later mora also starts a chunk when
   its attack is at least as strong as that 45% line. The line is set before
   the split, so every other chunk start is the same as with `--kanji keep`.
   With `auto`, kanji with no chunk start inside them are then written back
   as kanji; the chunk starts do not change.
   A unit that starts 1 second or more after the unit before it in the same
   line (after a long note or a rest) also starts a chunk, with or without a
   strong attack (`gap` in the parameters; 0 turns it off).
   The 45% line is set for each language on its own units, so a Japanese
   line keeps its chunk starts in a song that also has English or Korean.

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

**Chunks inside a kanji** (`--kanji auto` or `hiragana`, measured 2026-11-19). All
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

**English and Spanish** (measured 2026-11-19 on JamendoLyrics MultiLang, 20
songs each, vocals separated with Demucs, `--level dai --anchor attack`).
Recall is the share of the dataset's word starts matched within 50 ms. The
lags were tuned on these songs (both halves of each set agreed), so the
numbers after tuning are optimistic.

| | Words | Recall, lag −0.02 | Recall, tuned lag |
|---|---|---|---|
| English | 5,693 | 53.6% (95% interval 52.4 to 54.9%) | 55.0% (`lag_en` −0.03) |
| Spanish | 5,269 | 55.4% (54.0 to 56.7%) | 60.3% (`lag_es` 0.00) |

This is far below the 90% target. The biggest causes: in English, 14.6% of
the words were placed more than 1 second away (whole passages aligned to the
wrong place), and many more were 50 to 100 ms off. In Spanish, 782 words
joined to the word before by sinalefa never start a chunk at this level;
without them, recall is 65.0% before tuning. Korean, and the owner's own
songs in every language, are not measured yet.

Turn a JamendoLyrics annotation into a reference with

```
kizami import-ref --format jamendo annotations/words/SONG.csv \
  --words lyrics/SONG.words.txt --lyrics lyrics/SONG.txt -o ref.json
```

Its times mark the attack, so generate with `--anchor attack --level dai`
before `kizami eval`. A Korean song of the Children's Song Dataset becomes a
reference with one chunk per Hangul block (compare with `--level sho`):

```
kizami import-ref --format csd csv/kr001a.csv --lyrics lyric/kr001a.txt -o ref.json
```
 To try parameter values on songs whose model outputs
are cached, without running the models again:

```
python -m kizami.sweep song1 song2 --model-dir models --cache cache \
  --level dai --anchor attack --param lag_en=-0.06:0.02:0.01
```

## From Python

```python
from kizami.generate import generate

result = generate("songs/demo", model_dir="models", levels=["sho"], progress=print)
for level in result.levels:
    print(level.level, level.chunks, level.labels)
```

`generate` raises `kizami.generate.GenerateError` with a message for the user
when an input or a model file is missing.
