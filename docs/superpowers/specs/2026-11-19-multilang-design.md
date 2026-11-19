# Design: English, Korean, Spanish and mixed-language songs

Date: 2026-11-19. Status: draft for the owner's approval. Reviewed once by a builder (questions on the chu threshold, data flow and tuning answered in 5.3 and 7).
Builds on branch `claude/project-thread-5ux3l9` (PR #8, commit 6b9bd77:
furigana dropped, `--kanji auto`).

## 1. Intent

Kizami syncs lyric chunks to strong vocal attacks, like a rhythm game for the
voice. Today it works for Japanese only. The owner wants the same result for
Korean, English and Spanish, and for songs that mix them. Their order of
priority is:

1. K-pop (Korean with English)
2. Japanese versions of K-pop (Korean or Japanese, often with English)
3. Latin urban (Spanish)
4. EDM (English)
5. R&B (English)
6. Rap (US or UK English)

The owner's target is 90% or more of chunk starts within ±50 ms for every
song, Japanese-only songs included (raised from 85% on 2026-11-19).

## 2. Goals, non-goals, anti-goals

**Goals**

- One lyrics file can hold Japanese, Korean and Latin-script text in any mix,
  also inside one line. Kizami finds the language of each part by itself.
- The medium level (`chu`, the only level used) means the same in every
  language: a chunk starts at each word, at the strongest 45% of the attacks
  inside words, and after a gap of 1 s or more (`docs/generate.md`).
- Chunk start times follow the owner's convention in every language (end of
  the previous sound, `--anchor prev-end`).
- Accuracy is measured per language before any claim is made (section 7).

**Non-goals**

- A song that has both English and Spanish (owner, 2026-11-19). Latin-script
  text in one song is all English or all Spanish.
- Other languages (French, German, Chinese ...). The design should not block
  them, but they are not tested.
- Lyrics written in romanization (romaji, Korean in Latin letters). Write
  lyrics in their own script.
- The Japanese accuracy gap to 90%. Japanese `chu` is 87.2% on song 1 today.
  Proposed as separate work after this one.

**Anti-goals** (failure even if it runs)

- Any change to Japanese chunk starts on a Japanese-only line.
- A chunk boundary that cuts a Hangul syllable block or a Latin letter group
  that is not a syllable boundary.
- Downloading a model while the tool runs. Models stay files in the model
  folder, as now.
- Lyrics or audio of evaluation songs in the repository (they stay in the
  git-ignored `eval/data/`).

## 3. Owner's decisions (2026-11-19)

| # | Question | Decision |
|---|---|---|
| 1 | Priority | K-pop > Japanese-version K-pop > Latin urban > EDM > R&B > rap |
| 2 | Korean unit | One Hangul syllable block is the smallest unit (사/랑). A word is a space-separated group (사랑을). |
| 3 | English / Spanish inside a word | A chunk may start inside a word at a strong attack (beau / ti / ful). |
| 4 | Spanish vowel linking (sinalefa) | `mi amor` sung as `mia-mor`: the two words form one chunk. |
| 5 | Spaces | Keep a space at every chunk boundary, as for Japanese (Claude's reading of "スペース入れましょう", told to the owner). |
| 6 | English and Spanish in one song | Not supported. |
| 7 | English words in Japanese songs | Follow the written text: Latin letters are aligned as English, katakana as Japanese. |
| 8 | Test songs | The owner supplies songs per language and one mixed song; they can judge Korean and Spanish by ear only partly. |
| 9 | Target | 90% within ±50 ms in every language, Japanese included. |

## 4. Approaches considered

- **A. MMS only.** Align Korean, English and Spanish with the multilingual
  MMS_FA model that is already in the model folder (`mms_fa/model.pt`,
  1.26 GB). No new download. Risk: MMS learned from read speech; for Japanese,
  MMS alone was not enough and a Japanese model leads.
- **B. A dedicated model per language plus MMS.** The same shape as Japanese
  today: a wav2vec2 CTC model per language leads, MMS is the second opinion.
  About 3 to 4 GB more models, and one more model run per language in a song.
  Gain unknown until measured.
- **C. A first, then B where needed (chosen).** Build A, measure each language
  (section 7), and add a dedicated model only for a language below target.
  Everything A needs is also needed by B, so no work is lost.

Status: C is Claude's recommendation (2026-11-19 13:45Z). The owner has not
objected; confirm with this document.

## 5. Design

### 5.1 Script and language of each character

Each lyric line is cut into runs by script:

| Script | Characters | Language |
|---|---|---|
| Japanese | hiragana, katakana, kanji, `ー`, `々` | `ja` |
| Hangul | Hangul syllables (U+AC00 to U+D7A3) and jamo | `ko` |
| Latin | letters with or without accents, and `'` or `-` between letters | `en` or `es` for the whole song |
| other | digits, punctuation, spaces | belongs to no unit (as now) |

Latin language per song: `--latin auto|en|es` (default `auto`). `auto`
counts Spanish signs over all Latin words of the song: the letters
`ñ á é í ó ú ü ¿ ¡` and common Spanish-only words (`que`, `el`, `los`, `las`,
`del`, `para`, `pero`, `porque`, `cuando`, `yo`, `tú`, `mi`, `contigo` ...)
against common English-only words (`the`, `and`, `you`, `I`, `is`, `my`,
`love`, `baby` is neutral ...). Spanish wins when its score is higher; ties
go to English. The choice is printed (`latin: es`) and stored in the JSON
`meta`. The web app gets the same select (Auto / English / Spanish).

Reading overrides keep working in every script: `{base|sung}` gives the
sung text, and its language is taken from the sung text. The short form
`漢字{かな}` stays Japanese only.

### 5.2 Units

A unit is the smallest piece that can start a chunk, as now.

- **Japanese runs:** exactly today's path (SudachiPy, `atoms_for`), run on the
  Japanese run only, with offsets shifted to the line. A line with no other
  script must give the same units as today (test against the current code).
- **Korean runs:** a word is the text between spaces or punctuation. Each
  Hangul syllable block is one unit. Flags: first block of a word has
  `word_start`; `dependent` is always false (particles are already written
  inside the word).
- **English runs:** words split at spaces and punctuation, except `'` and `-`
  inside a word (`don't`, `rock-n-roll`). Each word is split into syllables by
  spelling rules: vowel groups (`a e i o u`, `y` when not first) form the
  nuclei; a final silent `e` (and `-es`, `-ed` after most consonants) adds no
  syllable; between nuclei one consonant goes to the next syllable, two are
  split, and the digraphs and onsets `ch sh th ph wh ck ng qu` and
  `bl br cl cr dr fl fr gl gr pl pr sc sk sl sm sn sp st sw tr tw str spr scr spl thr shr`
  are not split; `-le` after a consonant is its own syllable. A short
  exception table fixes common lyric words the rules get wrong. A wrong split
  only matters where a strong attack also falls, so the rules may be simple.
- **Spanish runs:** words as for English. Syllables by the standard rules:
  strong vowels `a e o`, weak `i u`; weak+strong, strong+weak and weak+weak
  form one syllable (diphthong); two strong vowels or an accented weak vowel
  (`í ú`) next to a strong one are split (hiatus); `qu` and `gu` before `e i`
  are one consonant; one consonant goes to the next syllable; `ch ll rr` and
  the clusters `pl pr bl br cl cr dr tr fl fr gl gr` are not split; silent `h`
  does not break a diphthong.
- **Sinalefa (Spanish):** when a word ends with a vowel (or `y`) and the next
  word starts with a vowel or `h`+vowel, the next word's first unit gets
  `word_start` false. It then starts a chunk only if its own attack is among
  the strongest in-word attacks, so `mi amor` stays one chunk unless the
  singer clearly attacks the `a`.
- **English inside a Japanese line:** Latin words follow the Latin rules above
  (no longer one `*` per vowel group).

Each unit gets `lang` and `letters`: the lower-case MMS letters for it.

- `ja`: today's `align.romaji(kana)`.
- `en`, `es`: the spelling, accents removed (`á` → `a`, `ñ` → `n`, `ü` → `u`),
  only `a` to `z` and `'` kept.
- `ko`: a built-in romanizer (Revised Romanization of each block's jamo,
  standard library only). Liaison: a final consonant before a block that
  starts with silent `ㅇ` moves to that block (`음악` → `eu` + `mak`), so each
  block's letters start with the sound that is sung at its start. Other sound
  changes are left out unless measurement shows a need.

### 5.3 Alignment

- **Kana model pass** (`w2v2-hiragana`): only when the song has Japanese
  units. Japanese units use their kana; every non-Japanese unit uses one `*`
  token per unit ("anything but silence"), as Latin words do today.
- **MMS pass:** required when the song has any non-Japanese unit (error
  message names `mms_fa/model.pt` if it is missing). Every unit uses its
  `letters`. Still optional for Japanese-only songs, as now.
- **Rough time per unit:** Japanese units: today's `fused_rough` (kana model
  leads, MMS where the kana model is unsure). Other units: the MMS time of
  their first letter plus `lag[lang]`. `lag` becomes a per-language value
  (`ja` keeps -0.02 s; `en`, `es`, `ko` start at -0.02 s and are tuned on the
  public sets, section 7). The snap window and `m_share` stay shared until a
  measurement shows a language needs its own.
- Snapping, the start rules (prev-end, legato, silence, gap) and the 1 s gap
  rule are unchanged.
- The `chu` threshold is computed **per language**: the strongest 45% of the
  in-word attacks of that language's units. Reasons: "45%" then means the same
  in every language, and Japanese units get exactly today's threshold even in
  a mixed song (today a Latin word is one unit and never feeds the threshold;
  English and Korean syllables would otherwise move it). Claude's call after
  the builder review.
- `place_morae` stays Japanese only. For other languages a unit is one
  syllable, so it has no inner starts.
- With approach B later, a language model is one more pass over the whole
  song, exactly like the kana model pass, and leads for its language.

### 5.4 Output

- Timing JSON: unchanged format. `meta` gains `languages` (the languages
  found, such as `["ko", "en"]`) and `latin`. The per-chunk `morae` list
  stays as it is for Japanese and is filled for other languages with one
  entry per syllable unit (`kana` holds the syllable text), so players can
  light up inside a chunk.
- Chunk text keeps the written spaces (f375620), so a chunk that ends a word
  ends with a space and a chunk that ends inside a word does not.
- TTML: with chunk spaces on (the default), every chunk is followed by a
  space, in every language (decision 5). With chunk spaces off, a space comes
  only where the chunk text has one. `_needs_space` (which adds a space
  between two touching Latin chunks) is removed for documents from
  `kizami generate`, because it would cut English words apart; it stays for
  hand-made files without spaces. `xml:lang` on `<tt>` is the main language
  of the song (most units); `--lang` still overrides it.
- `kanji_unless_split` (PR #8) only looks at kanji, so it needs no change.

### 5.5 Interfaces

- `kizami generate ... --latin auto|en|es`
- `kizami app`: Latin language select next to the kanji options.
- `kizami.generate.generate(..., latin="auto")`
- `GenerateResult` gains `languages: dict[str, int]` (units per language).

## 6. Constraints

- Local only. Models are read from `--model-dir`; nothing is downloaded at
  run time. The cloud container cannot run the models (no torch, no Hugging
  Face); all model runs and measurements happen on the owner's Mac.
- New Python dependencies: none for approach A. The romanizer and the
  syllable rules use the standard library only.
- Processing time target stays 1 to 3 minutes for a 4-minute song. A mixed
  Japanese and Korean song runs both models, as a Japanese song with MMS does
  today.
- Japanese regression guard: the chunk starts of the four Japanese reference
  songs must not change for Japanese-only lines (compare JSON before and
  after; only units inside or next to Latin text may move). Because those
  songs may hold few or no Latin words, a synthetic test also checks that a
  Japanese line gives the same units and the same `chu` threshold with and
  without English or Korean lines elsewhere in the song.

## 7. Measurement

All sets stay in git-ignored `eval/data/`. Only counts and scores are
reported or committed.

| Set | Languages | What it measures | Convention |
|---|---|---|---|
| JamendoLyrics MultiLang (Hugging Face `jamendolyrics/jamendolyrics`) | English 20 songs, Spanish 20 songs | word starts, human labels, after Demucs | attack (`--anchor attack`), level `dai` |
| CSD, Children's Song Dataset (Zenodo, CC BY-NC-SA 4.0) | Korean 50, English 50 | syllable starts (one note per syllable) | attack, level `sho` |
| Owner's songs, one chorus each, corrected in the tagger | K-pop KO+EN, J-version KO+JA, Latin urban ES, EDM, R&B, rap | the real target: `chu` starts | owner's (`prev-end`) |
| The four Japanese songs | JA | regression | owner's |

- New converters: `kizami import-ref --format jamendo|csd` turns a word or
  syllable annotation file plus its lyrics into a reference timing JSON, so
  `kizami eval` can compare it.
- Public sets measure the aligner and the snapping; their times mark the
  attack, about 0.1 s after the owner's chunk starts, so they are run with
  `--anchor attack`. Only the owner's songs decide the 90% target.
- Proxy for splits inside words (no public set has them): as for Japanese,
  time two neighbouring one-syllable words as if they were one word and check
  the second start.
- Pass: recall 90% or more within ±50 ms on the owner's chorus of each
  language (Claude's reading of decision 9; precision target stays 80%).
  Report the Wilson 95% interval, since one chorus has only 40 to 80 starts.
- Held-out rule: parameters are tuned only on the public sets and on Japanese
  songs 1 to 3. The owner's new songs are never used for tuning, so their
  score stays an honest check. If a language misses and can only be fixed by
  tuning on the owner's songs, ask the owner for a second song of that
  language first and keep one back.
- Where the work runs: the models run on the owner's Mac (remote session or
  the owner's folder). Model outputs (`--cache` `.npy` files) and vocal tracks
  may be copied to the cloud container for tuning sweeps; they are never
  committed. Lyric text may be read in the session but never committed, and
  reports in the repository hold counts and scores only. A sweep command that
  runs placement over cached model outputs for many parameter values (no model
  run) is part of step 4.

## 8. Order of work

1. Units and languages (5.1, 5.2) with unit tests; Japanese regression test.
2. MMS letters and alignment for all languages (5.3).
3. `--latin`, output and TTML space rule (5.4, 5.5).
4. Converters and measurement on the public sets (section 7), tune `mms_lag`.
5. The owner's songs, one per language, in priority order. Decide per
   language whether approach B is needed.

## 9. Left to the builder

- Module layout (for example `generate/lang.py` for script runs and
  syllables, `generate/hangul.py` for the romanizer).
- The exception table for English syllables and the word lists for the
  English or Spanish guess.
- How the Japanese run is fed to SudachiPy so that a Japanese-only line gives
  identical units (a test decides).
- Cache keys: the MMS cache key must not change for Japanese-only songs, so
  existing caches stay valid.

## 10. Open points for the owner

- Decision 5 reading (a space at every chunk boundary, also inside an English
  or Korean word) is Claude's reading.
- Approach C is Claude's recommendation.
- Rap: no published accuracy at ±50 ms is known to Claude; 90% is not
  promised.
