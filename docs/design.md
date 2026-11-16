# Kizami design

Status: Step 1 (evaluation tools and design). Decisions in this document were
made on 2026-11-15. Section 11 was added on 2026-11-16. Items marked
**unverified** are assumptions that Step 2 must test with measurements.

## 1. Purpose

Kizami makes timed lyrics for Japanese songs. Each piece of lyric text (a
*chunk*) lights up at the moment the singer starts to sing it. The effect
should feel like a rhythm game played by the vocal line.

Existing automatic tools have two problems:

- Timing made for speech drifts by about 200 to 500 ms when the voice is
  mixed with instruments.
- Japanese text is split mechanically, for example `夜 / 空を / 駆 / ける`,
  which breaks words in the middle.

## 2. Goals, non-goals, anti-goals

**Goals (MVP)**

- Input: one lyrics text file and one mixed audio file.
- Output: Kizami timing JSON (master data) and TTML. LRC and ASS on request
  (`--format lrc,ass`).
- Recall of 85% or more and precision of 80% or more at ±50 ms (section 5).
- Manual correction of 5 minutes or less for a 4-minute song.
- 1 to 3 minutes of processing for a 4-minute song on an Apple Silicon Mac.
- Singer parts (`ttm:agent`) from tags written in the lyrics file.
- Reading overrides for words that are sung with a non-standard reading.

**Non-goals (MVP)**

- Korean. English and other space-separated text gets word-level timing only.
- Transcribing lyrics from audio.
- Detecting which singer sings a line.
- Fast rap and dense harmonies.
- A graphical editor. Corrections are made in existing TTML editors.
- Video export.
- Looking up lyrics online. This is planned after the MVP as an opt-in
  feature (section 11).

**Anti-goals** (these make the tool a failure even if it runs)

- Splitting inside a word or compound such as `夜空`.
- Raising one score by gaming the other: many small chunks, or very few.
- Downloading audio. Kizami reads audio only from a file that the user
  already has on the local machine. It never fetches audio from a video or
  streaming service (section 11).
- Storing or distributing lyrics or audio. Kizami keeps no library and no
  cache of lyrics or audio.

## 3. Terms

| Term | Meaning |
|---|---|
| Attack | The first audible sound of a chunk, **including the consonant**. For `さ` it is the start of the `s` noise, not the start of the vowel. |
| Chunk | Lyric text that lights up as one unit. It starts at a morpheme boundary and is never shorter than one morpheme. |
| Boundary | The start of a chunk: a position in the lyric text plus a time. |
| Reference | Timing made by a person. It is the ground truth. |
| Generated | Timing made by Kizami. |

A long note (melisma, vibrato) is not split. It is one chunk from its attack
to the end of the sustained sound.

### Why the consonant start

If text lights up at the vowel, the display looks late for sounds with a long
consonant (`s`, `sh`, `ts`, `h`). This has a cost. Acoustic onset detectors
react to the fast energy rise, which is usually near the vowel. For a long
consonant the two points can be 50 to 150 ms apart (**unverified** estimate
from general phonetics), which is more than the ±50 ms tolerance. Step 2 must
measure this. See section 8.

## 4. Approaches considered

| | Approach | Trade-off |
|---|---|---|
| A | **Forced alignment, then snap to onsets.** A speech model places each mora roughly. Each candidate boundary then moves to the nearest strong onset in the vocal track. | Needs an alignment model that tolerates singing. Two stages to tune. Uses the known lyrics fully. **Chosen as first candidate.** |
| B | Onsets first. Detect all onsets, then assign morphemes to onsets with dynamic programming and mora counts. | No acoustic model of the text. One missed or extra onset shifts everything after it. |
| C | Speech recognizer word timestamps (Whisper family), then correct. | This is the method that already drifts by 200 to 500 ms. Its timestamps are a by-product, not a design target. |

Approach A is a hypothesis. Step 2 compares two or three concrete variants
on the reference data and keeps the one that measures best.

## 5. Evaluation

Evaluation comes first because parameter tuning without a measurement goes
in circles.

### 5.1 Metric

A **hit** is a reference boundary and a generated boundary that are at the
same text position and no more than 50 ms apart.

- **Recall** = hits / reference boundaries. Target: 85% or more.
- **Precision** = hits / generated boundaries inside the reference excerpt.
  Target: 80% or more.

Both must pass. Recall alone rewards splitting everywhere. Precision alone
rewards splitting almost nowhere.

Design calls made here:

- *Same text position is required.* A generated boundary at the right time
  but in front of the wrong text would light up the wrong word, so it is not
  a hit. This is stricter than matching by time only. The reported scores
  can only be lower than time-only scores, never higher.
- *Positions use normalized text* (NFKC, case folding, letters and digits
  only). Spacing, punctuation, and character width do not affect matching.
- *The reference can be an excerpt.* Kizami runs on the full song. The
  evaluation finds the excerpt in the generated file by its text. If the
  text repeats (a chorus sung three times), it uses the occurrence nearest
  in time.
- *Tolerance is inclusive.* A difference of exactly 50 ms is a hit.

The evaluation also reports, for diagnosis only:

- Signed timing error (generated minus reference) at shared positions:
  median, mean, median absolute, 90th percentile absolute. The median is
  the direct estimate for a global offset correction.
- Counts of missing splits, extra splits, too-late and too-early boundaries.
- Recall without the boundaries that the labeller marked as uncertain.
- 95% confidence intervals (Wilson score).
- End-time checks: missing end, zero or negative length, overlaps inside a
  line. End times are not a pass criterion.

### 5.2 Reference data

- Made by a person in Audacity on the separated vocal track. Tapping in
  real time is not precise enough for a ±50 ms tolerance. The procedure is
  in [labeling-guide.md](labeling-guide.md).
- Since 2026-11-16 the labeller starts from *candidates*: empty labels at
  the places where a sound starts, made by `kizami suggest-labels` from the
  audio alone. The labeller types the chunk text at the candidates that are
  chunk starts, checks each one by ear, and adds or moves labels where the
  candidate is missing or wrong. Placing every label by hand took too long.
- The reference holds start times only. End times are not a pass criterion,
  so the labeller does not mark them.
- **Review flow (main flow since 2026-11-16).** Kizami makes the timing for
  the full song. `kizami export-labels` writes it as Audacity labels. The
  labeller plays the song, corrects the labels that are wrong, and exports
  them. `kizami import-labels` turns the result into a *reviewed
  reference*. The labeller also notes the minutes spent. This is less work
  than labelling from nothing, and it is the same work that a user of the
  finished tool does. Its cost for the measurement is in section 5.3.
- File format: [timing-json-format.md](timing-json-format.md).
- First set: the chorus (30 to 45 s) of 3 songs with different tempos, used
  for tuning, plus 1 more song that is **held out**. The held-out song is
  never used to choose a method or a parameter. It is evaluated only when a
  version is declared finished.
- Reference files contain lyric text. They stay on the local machine in
  `eval/data/`, which Git ignores.

### 5.3 Limits of the measurement

- **Label noise.** The labeller relabels one chorus on another day and
  compares the two passes with the same tool. If the two passes agree on
  fewer than about 95% of boundaries, the 85% target cannot be measured
  reliably, and the procedure or the tolerance must change first.
- **Sample size.** Three choruses give an estimated 60 to 120 boundaries.
  At 100 boundaries, a measured 85% has a 95% interval of about 77% to 91%.
  This shows direction but cannot separate 85% from 80%. More data is added
  in Step 3.
- **Anchoring.** A reference that is made by correcting Kizami output is
  pulled toward the tool. A boundary that is 40 ms off and sounds
  acceptable stays where the tool put it, and then counts as a hit at 0 ms.
  The review flow accepts this to save labelling time. So a score against
  a reviewed reference is the **share of boundaries that the reviewer
  accepted**. It is an upper bound for the share within ±50 ms of the true
  attack, not a measurement of it. Three things keep the picture honest:
  - Every report says which kind of reference it used.
  - The review itself is measured: the number of labels that were moved,
    added, or removed (`kizami eval reviewed.json generated.json`), and the
    minutes spent. The minutes test the target of 5 minutes for a 4-minute
    song directly.
  - One chorus is labelled by hand on another day, without Kizami output
    and without candidates. It is the only independent reference. How to
    treat the held-out song is decided after the first reviewed results.
- **Candidate bias.** Candidates are a weaker form of anchoring. They come
  from an onset detector, and Kizami snaps to onsets too. Labels that sit
  on candidates can make the scores too good, most of all for long
  consonants, where a detector tends to mark the vowel. How large this
  effect is, is **unverified**. It is measured with a second labelling
  pass of one chorus on another day, by hand and without candidates. The
  held-out song may use candidates only if the two passes agree on 95% or
  more of the boundaries and the median difference is 10 ms or less.
- **Section bias.** Choruses often have doubled vocals and harmonies. Verses
  may behave differently. Step 3 adds verse sections.

## 6. Core pipeline (planned)

```
lyrics.txt ──► parse ──► morphemes + readings ──┐
                                               ├─► forced alignment ─► rough time per morpheme
audio ──► vocal separation ──► vocal track ────┤
                                               └─► onset detection ──► onset times + strengths
                                                                │
        rough times + onsets ──► snap and merge ──► chunks ──► end times ──► timing JSON ──► TTML / LRC / ASS
```

1. **Parse lyrics.** Lines, singer tags, reading overrides (section 7).
2. **Morphological analysis.** Split each line into morphemes. Morpheme
   starts are the only places where a chunk may start. A reading in kana is
   attached to each morpheme. A text with a reading override is one unit.
3. **Vocal separation.** `demucs` on the mixed audio.
4. **Forced alignment.** Align the kana sequence to the vocal track. Result:
   a rough start time for each morpheme.
5. **Onset detection.** An onset strength curve on the vocal track, and its
   peaks.
6. **Snap and merge.** For each morpheme start, look for the strongest onset
   inside a search window around the rough time.
   - Strong onset found: the morpheme starts a chunk at the onset time plus
     an offset correction.
   - No strong onset: the morpheme joins the previous chunk. This is how
     `を` after `夜空` stays inside `夜空を` when the singer does not
     re-attack.
   - The first morpheme of a line always starts a chunk.
   - A chunk shorter than a minimum duration joins its neighbour.
7. **End times.** A chunk ends where the voice stops, or where the next
   chunk of the same line starts, whichever is earlier.
8. **Write output.** Timing JSON first. Other formats are converted from it.

Tunable parameters (starting values are set in Step 2 from measurements):
search window before and after the rough time, onset strength threshold,
offset correction, minimum chunk duration.

## 7. Lyrics input syntax (planned, implemented in Step 3)

```
[ボーカルA]
あの宇宙{そら}を見上げて
[ボーカルB]
{Tonight|こんや}は帰らない
```

- One lyric line per text line. Empty lines separate stanzas.
- `[name]` on its own line sets the singer for the lines that follow, until
  the next tag. Each distinct name becomes one `ttm:agent`.
- `漢字{よみ}` gives the sung reading of the kanji run directly before the
  braces. `{text|よみ}` does the same for any text. The output shows only the
  base text. A wrong reading shifts every timestamp after it, so this
  syntax is part of the MVP.

## 8. Questions that Step 2 must answer with measurements

1. **Which aligner.** Model licences matter: some model weights are
   non-commercial even when the code is permissive. Kizami is meant for
   other users too, so a model without such a limit comes first. Read on
   2026-11-16, none of this is measured on singing yet:
   - `reazon-research/japanese-wav2vec2-base-rs35kh`: Apache 2.0 (model
     card). A wav2vec 2.0 model with a CTC output, trained on Japanese
     speech, 16 kHz input. The model card reports poor results on long
     audio, so the song must be aligned in sections. **First candidate.**
   - MMS forced-alignment model (`MMS_FA`): needs romanized text. A
     third-party page gives its licence as CC-BY-NC 4.0; the official
     licence text was **not checked**. The same page reports, for one song
     and this model alone, a median error of 67 ms per character and 82%
     within 200 ms. This fits the assumption that alignment alone does not
     reach ±50 ms and that snapping to onsets is needed.
   - The forced-alignment functions of `torchaudio` are marked as
     deprecated since version 2.8, with removal announced for 2.9
     (torchaudio documentation). Kizami therefore does the alignment step
     (CTC Viterbi) in its own code and uses a model only for the frame
     scores.
   - Whisper-based alignment and aligners made for singing: not checked.
   - **Baseline from an existing tool.** `lyric-align` (PyPI, MIT, version
     0.4.2 of 2026-10-17) also aligns given lyrics to sung audio, is made
     for Japanese and Chinese first, and writes TTML and Audacity labels.
     It should be run on the same songs. If Kizami is not clearly better at
     chunk starts, the extra stages are not worth their cost.
2. **Offset correction.** Compare no offset, one global offset (for example
   -20 to -40 ms), and an offset per sound class of the first mora (vowel,
   plosive, fricative, nasal). A single global offset may not be able to
   fix both vowel starts and long consonants. The error report will be
   split by sound class to test this.
3. **Separation quality.** Separation can blur weak consonants. Compare
   `demucs` with one alternative if consonant starts are the main source of
   misses.
4. **Morphological analyzer.** Candidates: SudachiPy (long-unit mode) and
   MeCab with UniDic. The choice depends on how well compounds stay whole
   and how often readings are wrong on lyric text.
5. **Processing time.** Measure each stage on an Apple Silicon Mac against
   the 1 to 3 minute budget.

## 9. Risks

| Risk | Handling |
|---|---|
| Time base differs between files. MP3 and AAC decoders can add different amounts of leading delay. | Convert the song to WAV once. Use that WAV for separation, labelling, and generation. |
| Model weights are code. Pickle-based weight files can run arbitrary code when loaded. | Download weights only from the official source of each model. Prefer `safetensors` files. Pin versions. |
| Copyright. | No lyrics or audio in the repository, in tests, or in issues. Tests use made-up text. |
| Overfitting to three choruses. | Held-out song. More data in Step 3. |

## 10. Plan

1. **Evaluation tools and design** (this step): timing JSON format, Audacity
   label converter, evaluation command, labelling guide, this document.
2. **Method comparison:** two or three alignment variants, offset
   correction, processing time, all measured with the Step 1 tools.
   On 2026-11-16 the order changed: a first automatic version is built
   before any reference exists, and the first references come from the
   review flow (section 5.2).
3. **Core implementation:** the pipeline in section 6, tuned to the targets.
4. **Output and finish:** TTML with `ttm:agent`, LRC, ASS, user README,
   licence file, test automation.

## 11. After the MVP: opt-in lyrics lookup

Decided on 2026-11-16. This is not part of Steps 1 to 4. Work on it starts
only after the core passes its targets on the held-out song.

The lookup is for every user of Kizami, not only for the author. The author
expects to process many songs, and getting the lyrics text is the main
manual effort. Timing is made for the local audio file.

**What was asked for:** give a video link, get the audio and its details,
find the lyrics in a lyrics service, and fall back to the user's own lyrics
file when the service has none.

**What was decided**

| Part | Decision |
|---|---|
| Audio from a video or streaming link | Never built in. |
| Song details (title, artist, album, duration) | Read from the tags of the local audio file. |
| Lyrics lookup in an online service | Added after the MVP. Off by default. |
| The user's own lyrics file | Always wins when it is given. Required when the lookup finds nothing. |

**Why audio download is not built in**

- The YouTube Terms of Service (effective 2023-06-01, read on 2026-11-16)
  do not allow downloading except where the service provides it, and do not
  allow access by automated means.
- Timing must be made from the same audio that will be played. A music
  video can differ from the released track, for example in the length of
  the intro. Timing made from one then plays shifted on the other. How often
  this happens is **unverified**.
- A purchased or ripped file has tags that are more exact than a video
  title. Tags are the better key for a lyrics lookup.

**Rules for the lookup**

- It runs only when the user asks for it with an option. Without the
  option, Kizami makes no network request. "Everything runs on your
  computer" stays true by default.
- It sends the title, artist, album, and duration of the song. It never
  sends audio, file paths, or anything that identifies the user. The user
  documentation must say what is sent and to which service.
- The service sits behind a small interface, so that it can be replaced.
- The user checks the fetched text before alignment starts. Library lyrics
  can leave out repeated sections, can use different kanji or kana than the
  user wants to show, and have no singer tags and no reading overrides
  (section 7).
- Fetched text is treated as untrusted input. It goes through the same
  parser as a lyrics file.
- Requests use HTTPS, identify the client by name and version, go one at a
  time, and obey the rate limits of the service.

**First service candidate: LRCLIB** (documentation read on 2026-11-16)

- No API key. Lookup by track name and artist. Album and duration are
  optional. A duration matches within 2 seconds.
- Returns plain lyrics and line-timed lyrics (LRC).
- About 45 requests per minute for an anonymous client, about 90 for a
  client that identifies itself.
- The documentation gives no licence terms for the lyric text. The data is
  entered by users. Check the terms again before this feature is released.

**Open questions**

1. Do line times from a lookup help the alignment? They could narrow the
   search to one line. **Unverified.** Step 2 can test this as a side
   experiment with line times that are supplied by hand. Line times made
   for a different version of the song would hurt, so they can only be a
   hint, never a constraint.
2. Which lyrics service may be used in a public release. Because the
   lookup is for every user, the terms of the service must be checked
   before the feature is released. LRCLIB gives no terms for its lyric
   text. Licensed services are not investigated.
3. Which tag formats to read first (MP4, ID3, Vorbis comments), and whether
   this needs a runtime dependency.
