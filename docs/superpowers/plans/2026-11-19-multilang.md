# Multilingual Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `kizami generate` places chunks for Korean, English and Spanish lyrics and for any mix with Japanese, with Japanese-only lines unchanged.

**Architecture:** A pure-Python language layer (script runs, Latin language guess, Hangul letters, English and Spanish syllables) feeds `text.atoms_for`, which now tags every unit with `lang` and `letters`. Japanese units keep the kana model path; all other units take their rough time from the MMS letter model. Placement, the `chu` threshold (per language) and output stay one pipeline.

**Tech Stack:** Python 3.10+, standard library for all language code; numpy, torch/torchaudio (MMS_FA), transformers, SudachiPy as today.

**Spec:** `docs/superpowers/specs/2026-11-19-multilang-design.md`

## Global Constraints

- No new Python dependency. Language code uses the standard library only.
- Nothing is downloaded at run time; models come from `--model-dir`.
- Japanese-only lines: units, times and chunk starts identical to `origin/main` 7a1d612.
- Units: `ja` as today; `ko` one Hangul block; `en`/`es` one syllable. Word start = first unit of a space-separated word; `dependent` only for Japanese.
- MMS letters: `a`-`z` and `'` only. Korean letters follow uroman's tables (lead `g gg n d dd r m b bb s ss - j jj c k t p h`, vowel `a ae ya yae eo e yeo ye o wa wai oe yo u weo we wi yu eu yi i`, tail `- g gg gs n nj nh d l lg lm lb ls lt lp lh m b bs s ss ng j c k t p h`), plus liaison inside a word.
- Lag per language: params `lag` (ja, -0.02), `lag_en`, `lag_es`, `lag_ko` (each -0.02 until tuned).
- `chu` threshold per language: the (1 - `m_share`) quantile of that language's in-word snapped strengths.
- `--latin auto|en|es`, default `auto`. A song with both English and Spanish is not supported.
- Lyric text never goes into the repository or into printed reports.
- Code, comments, docs in English.

## Review Focus

1. A line that mixes scripts with no space between them (`Baby사랑해`, `君のsmile`): each run gets its own units and the first unit of each run starts a word.
2. Apostrophes and hyphens inside words (`don't`, `rock-n-roll`, `'cause`): one word, syllables never split at the apostrophe; a leading apostrophe stays with the word.
3. A Latin word with no vowel letter (`hmm`, `shh`, `brr`): one unit, letters as spelled, no crash.
4. A song with Korean or English only: the kana model is not run and not required; MMS missing gives a clear error naming `mms_fa/model.pt`.
5. Uppercase and accented input (`CORAZÓN`, `Ñ`, full-width `ＬＯＶＥ`): same units as lower-case ASCII after NFKC; letters lower-case without accents.

---

### Task 1: Script runs and the Latin guess

**Files:**
- Create: `src/kizami/generate/lang.py`
- Test: `tests/test_lang.py`

**Interfaces:**
- Produces: `script_of(c: str) -> str` returning `"ja"`, `"ko"`, `"latin"` or `""`; `script_runs(text: str) -> list[tuple[int, int, str]]` (start, end, script) over a line; `guess_latin(words: Iterable[str]) -> str` returning `"en"` or `"es"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_script_of():
    assert [script_of(c) for c in "あア漢ー々한aÑ1 ,"] == ["ja"] * 5 + ["ko", "latin", "latin", "", "", ""]

def test_runs_split_at_script_changes_and_keep_inner_spaces():
    assert script_runs("I love you 君が好き") == [(0, 10, "latin"), (11, 15, "ja")]
    assert script_runs("Baby사랑해") == [(0, 4, "latin"), (4, 7, "ko")]
    assert script_runs("「君と」 dance") == [(1, 3, "ja"), (6, 11, "latin")]

def test_guess_latin():
    assert guess_latin("yo quiero bailar contigo".split()) == "es"
    assert guess_latin("I want you baby".split()) == "en"
    assert guess_latin(["corazón"]) == "es"
    assert guess_latin([]) == "en"
```

- [ ] **Step 2: Run** `pytest tests/test_lang.py -q` — expect ImportError.
- [ ] **Step 3: Implement** in `lang.py`. Runs: a run starts and ends on a letter; neutral characters between two letters of the same script belong to the run; neutral characters at a script change or at the line ends belong to no run. Latin guess: score +2 per `ñ á é í ó ú ü ¿ ¡`, +1 per Spanish-only word, -1 per English-only word (lists in the module, about 40 words each); `es` when the score is above 0.
- [ ] **Step 4: Run** `pytest tests/test_lang.py -q` — PASS.
- [ ] **Step 5: Commit** `git commit -m "Generate: find the script of each part of a lyric line"`

### Task 2: Hangul letters

**Files:**
- Create: `src/kizami/generate/hangul.py`
- Test: `tests/test_hangul.py`

**Interfaces:**
- Produces: `block_letters(c: str) -> tuple[str, str, str]` (lead, vowel, tail, uroman tables, `-` lead written as `""`); `word_letters(word: str) -> list[str]` (one letter string per Hangul block, liaison applied).

- [ ] **Step 1: Write the failing tests**

```python
def test_block_letters_follow_uroman():
    assert block_letters("사") == ("s", "a", "")
    assert block_letters("랑") == ("r", "a", "ng")
    assert block_letters("차") == ("c", "a", "")

def test_liaison_moves_a_final_consonant_to_a_silent_lead():
    assert word_letters("음악") == ["eu", "mag"]
    assert word_letters("사랑해") == ["sa", "rang", "hae"]
    assert word_letters("읽어") == ["il", "geo"]      # double tail: second part moves
    assert word_letters("좋아") == ["joh", "a"]       # h stays
    assert word_letters("사랑이") == ["sa", "rang", "i"]  # the ng tail never moves
    assert "".join(word_letters("음악")) == "eumag"
```

- [ ] **Step 2: Run** `pytest tests/test_hangul.py -q` — ImportError.
- [ ] **Step 3: Implement.** Liaison: when block i has a tail other than `ng` or `h`/`nh`/`lh` endings and block i+1 has the silent lead, move the tail (or the second letter group of a double tail: `gs`→`s`, `nj`→`j`, `lg`→`g`, `lm`→`m`, `lb`→`b`, `ls`→`s`, `lt`→`t`, `lp`→`p`, `bs`→`s`) to the start of block i+1. The joined string never changes.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `git commit -m "Generate: Korean letters for the letter model"`

### Task 3: English and Spanish syllables

**Files:**
- Create: `src/kizami/generate/syllables.py`
- Test: `tests/test_syllables.py`

**Interfaces:**
- Produces: `syllables(word: str, lang: str) -> list[str]` (pieces of the written word, joined they equal the word, `lang` is `"en"` or `"es"`); `latin_letters(s: str) -> str` (NFKD, accents removed, `ñ`→`n`, lower-case, only `a`-`z` and `'`); `links(prev_word: str, next_word: str) -> bool` (Spanish sinalefa: previous ends with a vowel or `y`, next starts with a vowel or `h`+vowel).

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("word, parts", [
    ("beautiful", ["beau", "ti", "ful"]), ("love", ["love"]), ("baby", ["ba", "by"]),
    ("little", ["lit", "tle"]), ("together", ["to", "ge", "ther"]), ("don't", ["don't"]),
    ("dancing", ["dan", "cing"]), ("hmm", ["hmm"]), ("Tonight", ["To", "night"]),
])
def test_english_syllables(word, parts):
    assert syllables(word, "en") == parts

@pytest.mark.parametrize("word, parts", [
    ("corazón", ["co", "ra", "zón"]), ("quiero", ["quie", "ro"]), ("noche", ["no", "che"]),
    ("perro", ["pe", "rro"]), ("país", ["pa", "ís"]), ("poeta", ["po", "e", "ta"]),
    ("hablar", ["ha", "blar"]), ("contigo", ["con", "ti", "go"]), ("ahora", ["a", "ho", "ra"]),
])
def test_spanish_syllables(word, parts):
    assert syllables(word, "es") == parts

def test_latin_letters():
    assert latin_letters("CORAZÓN") == "corazon" and latin_letters("Ñandú") == "nandu"
    assert latin_letters("don't") == "don't" and latin_letters("ＬＯＶＥ") == "love"

def test_links():
    assert links("mi", "amor") and links("que", "hago") and not links("mi", "vida") and not links("amor", "eterno")
```

- [ ] **Step 2: Run** — ImportError.
- [ ] **Step 3: Implement** the rules of spec 5.2 with an exception table `EN_EXCEPTIONS: dict[str, list[str]]` (lower-case keys, case restored from the input). Keep `-` inside a word as a split point that belongs to the piece before it.
- [ ] **Step 4: Run** — PASS. Add a failing case to the exception table rather than bending a rule when one word disagrees.
- [ ] **Step 5: Commit** `git commit -m "Generate: English and Spanish syllables"`

### Task 4: Units for every language

**Files:**
- Create: `src/kizami/generate/letters.py` (move `ROMAJI`, `SMALL_Y`, `SMALL_V`, `VOWELS`, `romaji` out of `align.py`; `align.py` re-imports them)
- Modify: `src/kizami/generate/text.py` (`atoms_for`)
- Create: `tests/data/ja_units_golden.json` (fixture, Japanese only, no song lyrics: use the test sentences already in `tests/test_generate.py` plus three made-up lines)
- Test: `tests/test_generate.py`

**Interfaces:**
- Consumes: Task 1 `script_runs`, `guess_latin`; Task 2 `word_letters`; Task 3 `syllables`, `latin_letters`, `links`.
- Produces: `atoms_for(lines, vocab, latin="auto") -> tuple[list[dict], int, str]` — units, kanji compounds kept whole, and the Latin language used. Every unit gains `lang` (`ja`/`ko`/`en`/`es`) and `letters` (str). Non-Japanese units have `kana == ["*"]`, `hiragana is None`, `dependent False`, and `syllable` (their written text). Sinalefa sets `word_start False` on the linked unit.

- [ ] **Step 1: Make the golden fixture from the current code** (before any change): a script in the test file's docstring documents how; store `[(line, offset, end, kana, hiragana, word_start, dependent, line_first)]` per unit.
- [ ] **Step 2: Write the failing tests**

```python
def test_japanese_units_match_the_golden_file(): ...            # atoms_for on the fixture lines == fixture
def test_japanese_line_is_the_same_next_to_other_languages(): ... # units of a JA line unchanged when EN and KO lines are added
def test_korean_units_are_blocks_and_words_start_after_spaces():
    a = units("사랑해 너를")
    assert [u["syllable"] for u in a] == ["사", "랑", "해", "너", "를"]
    assert [u["word_start"] for u in a] == [True, False, False, True, False]
    assert [u["letters"] for u in a] == ["sa", "rang", "hae", "neo", "reul"]
def test_english_units_are_syllables(): ...   # "beautiful baby" -> beau ti ful ba by, word starts at beau and ba
def test_spanish_sinalefa_joins_the_next_word(): ...  # "mi amor" -> word_start of "a" is False
def test_mixed_line_without_spaces(): ...      # "Baby사랑해": Ba by 사 랑 해, word starts at Ba and 사
def test_latin_guess_is_returned(): ...        # atoms_for(lines, vocab)[2] == "es" for a Spanish line
def test_japanese_units_carry_romaji_letters(): ...  # letters == romaji(kana)
```

- [ ] **Step 3: Run** `pytest tests/test_generate.py -q -k "units or golden or sinalefa or latin or romaji"` — FAIL.
- [ ] **Step 4: Implement.** Per line, cut with `script_runs`. A `ja` run goes through today's Sudachi path on the run text with offsets shifted (a line whose only script is `ja` passes the whole line to today's path, brackets included, so the golden test holds). `ko` and `latin` runs: split words at spaces and punctuation (not `'`/`-` inside a word), one unit per Hangul block or syllable; `line_first` only on the line's first unit; `word_start` on each word's first unit. Update every caller of `atoms_for` (`generate/__init__.py`) for the third return value.
- [ ] **Step 5: Run** the full suite `pytest -q` — all pass.
- [ ] **Step 6: Commit** `git commit -m "Generate: units for Korean, English and Spanish"`

### Task 5: Alignment and placement by language

**Files:**
- Modify: `src/kizami/generate/align.py` (`ctc_align`, `mms_align`, new `adopt_mms`)
- Modify: `src/kizami/generate/place.py` (`DEFAULTS`, `fused_rough`, `place`, `place_morae`, `chu_thresholds`, `select`)
- Test: `tests/test_generate.py`

**Interfaces:**
- Consumes: units from Task 4.
- Produces: `mms_align(em, atoms)` uses `a["letters"]` and also sets `mms_last`; `adopt_mms(atoms) -> int` copies MMS times into `rough`, `rough_last`, `kana_starts = [rough]`, `conf = mms_conf` for every non-`ja` unit and returns how many; `chu_thresholds(atoms, share) -> dict[str, float]`; `select(atoms, level, m_thr: float | dict[str, float], gap=None)`; `DEFAULTS` gains `lag_en`, `lag_es`, `lag_ko` = -0.02; `lag_for(a, p) -> float`.

- [ ] **Step 1: Write the failing tests**

```python
def test_mms_letters_come_from_the_units(): ...          # fake emissions: ids follow "".join(letters)
def test_non_japanese_units_take_the_mms_time(): ...    # adopt_mms sets rough = mms for ko/en/es, leaves ja untouched
def test_fusion_offset_uses_japanese_units_only(): ...  # fused_rough median offset ignores non-ja units
def test_lag_is_read_per_language(): ...                # place with lag_en=-0.05 moves only en units' search centre
def test_threshold_per_language_keeps_japanese(): ...   # chu_thresholds({ja..., en...})["ja"] == chu_threshold(ja units only)
def test_select_uses_the_unit_language_threshold(): ...
def test_non_japanese_units_list_their_syllable_as_one_mora(): ... # place_morae -> [(syllable, time, snapped)]
def test_sibilant_from_letters(): ...                   # an en unit "so" uses the hiss start like さ
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.** `ctc_align` unchanged except it is skipped by the caller when no unit is `ja`. Sibilant check: `ja` by kana as now; others when `letters` starts with `s`, `sh`, `ch`, `c`+`e`/`i` (es), `z`, `ts`, `j` (ko). `chu_threshold` stays for compatibility and equals `chu_thresholds` over all units of one language.
- [ ] **Step 4: Run** `pytest -q` — all pass.
- [ ] **Step 5: Commit** `git commit -m "Generate: align and place units of every language"`

### Task 6: The generate command

**Files:**
- Modify: `src/kizami/generate/__init__.py`, `src/kizami/cli.py`, `src/kizami/app/__init__.py`, `src/kizami/app/index.html`
- Test: `tests/test_generate.py`, `tests/test_cli.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: Tasks 4 and 5.
- Produces: `generate(..., latin="auto")`; `GenerateResult.languages: dict[str, int]` and `GenerateResult.latin: str`; JSON `meta.languages` (units per language, such as `{"ko": 120, "en": 40}`) and `meta.latin`; CLI `kizami generate --latin auto|en|es`; app field `latin` (select Auto / English / Spanish).

- [ ] **Step 1: Write the failing tests:** `latin` outside `auto|en|es` raises `GenerateError`; a Korean-only song with no `mms_fa/model.pt` raises `GenerateError` whose message contains `mms_fa/model.pt`; a Korean-only song does not need `w2v2-hiragana/` (monkeypatch the model runs, as existing tests do); CLI passes `--latin es` through; the app form sends `latin`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.** Order: parse lyrics, build units, then check models (kana model only if a `ja` unit exists; MMS required if a non-`ja` unit exists, else today's `auto`). Run `ctc_align` only with `ja` units, `mms_align` + `adopt_mms` when MMS is used, then `place`, `place_morae`, `p["m_thr"] = chu_thresholds(...)`. Print `latin: en|es` in the CLI summary only when the song has Latin units.
- [ ] **Step 4: Run** `pytest -q` — all pass.
- [ ] **Step 5: Commit** `git commit -m "Generate: --latin and models chosen by the languages of the song"`

### Task 7: TTML spaces and language

**Files:**
- Modify: `src/kizami/ttml.py`, `src/kizami/cli.py`
- Test: `tests/test_ttml.py`

**Interfaces:**
- Produces: `document_to_ttml(doc, lang=None, ...)`: `lang None` → most common entry of `meta.languages` mapped to `ja`/`ko`/`en`/`es`, else `"ja"`. CLI `--lang` default None.

- [ ] **Step 1: Write the failing tests:**

```python
def test_no_space_inside_an_english_word_when_chunk_spaces_are_off(): ...
    # generated doc, chunks "beau", "ti", "ful ", "ba", "by" -> text nodes give "beautiful baby"
def test_space_after_every_chunk_when_chunk_spaces_are_on(): ...   # "beau ti ful ba by"
def test_hand_made_latin_chunks_without_spaces_still_get_one(): ... # meta source "kizami tagger": old behaviour
def test_lang_comes_from_the_document(): ...  # meta.languages ["ko","en"] with more ko units -> xml:lang="ko"
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement:** skip `_needs_space` when `doc.meta.get("source") == "kizami generate"`; read `meta.languages` counts written by Task 6.
- [ ] **Step 4: Run** `pytest -q` — PASS.
- [ ] **Step 5: Commit** `git commit -m "TTML: no space inside a word unless asked, language from the lyrics"`

### Task 8: Reference converters and the sweep

**Files:**
- Create: `src/kizami/datasets.py`
- Modify: `src/kizami/cli.py` (`import-ref`)
- Create: `tools/sweep.py` (no lyric text in its output)
- Test: `tests/test_datasets.py`

**Interfaces:**
- Produces: `jamendo_reference(words_csv: Path, words_txt: Path, lyrics_txt: Path) -> TimingDocument` (one chunk per word, lines from the lyrics file, start = `word_start`, end = `word_end`); `csd_reference(csv_path: Path, lyrics_txt: Path) -> TimingDocument` (one chunk per syllable; columns decided after reading one real file on the Mac); CLI `kizami import-ref --format jamendo|csd ... -o ref.json`; `tools/sweep.py SONGS... --param lag_en=-0.06:0.02:0.01 --level dai --anchor attack` prints recall/precision per value, reusing `--cache` so no model runs.

- [ ] **Step 1: Write the failing tests** with tiny made-up CSV and text files in `tmp_path` (format `word_start,word_end,line_end` for Jamendo).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest -q` — PASS.
- [ ] **Step 5: Commit** `git commit -m "Eval: import JamendoLyrics and CSD references, parameter sweep"`

### Task 9: Docs

**Files:**
- Modify: `docs/generate.md` (languages, units, `--latin`, MMS required for non-Japanese), `README.md` (supported languages in plain words), `docs/design.md` (Korean and syllables no longer non-goals; link the spec), `docs/ttml.md` (`--lang` default).

- [ ] **Step 1:** Write the changes. **Step 2:** `pytest -q` still passes. **Step 3: Commit** `git commit -m "Docs: languages"`

### Task 10: Measurement on the Mac (no new code unless a test proves a bug)

- [ ] Japanese regression: run `kizami generate` on the four songs (cached model outputs) before and after; every chunk start on a line without Latin text is identical. Report counts.
- [ ] JamendoLyrics English and Spanish: download on the Mac into `eval/data/jamendo/`, separate with Demucs `--shifts 0`, generate with `--anchor attack --level dai`, `kizami eval` per song; report recall and precision within 50 ms per language with Wilson intervals.
- [ ] CSD Korean: same with `--level sho`; finish `csd_reference` columns first.
- [ ] Tune `lag_en`, `lag_es`, `lag_ko` with `tools/sweep.py` on the public sets only; keep values that raise recall on both halves of each set (split by song).
- [ ] Ask the owner for one chorus per language in priority order; their songs are never tuned on.
