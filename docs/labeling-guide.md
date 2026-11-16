# How to make reference timing

Reference timing is the ground truth that Kizami output is measured against.
A person makes it in Audacity by placing one label at the attack of each
chunk. This guide makes the result repeatable.

There are three ways to make it. All of them use point labels: you do not
mark where a chunk ends.

| Way | Your work | Use it for |
|---|---|---|
| Review (4.3) | Kizami places all labels. You correct the wrong ones. | Normal work, once Kizami can generate timing. |
| Candidates (4.1) | Kizami marks where sounds start. You type the text. | Songs or sections that Kizami cannot generate yet. |
| By hand (4.2) | You place every label. | The independent check in section 6. |

What was checked on a real machine (2026-11-15, macOS on Apple Silicon,
Python 3.14, demucs 4.1.0, torch 2.15.0, ffmpeg 9.0.2, Audacity 4.0.2):

- The setup commands in section 0 and the `demucs` command in section 1 ran
  without error. The input was a 10-second test tone. The `ffmpeg`
  conversion and the separation were **not yet run on a real song**.
- The Audacity menu names in section 4 were read from Audacity 4.0.2.
- **Not yet checked:** the content of a label file exported by Audacity 4,
  the Cmd+B shortcut, whether Audacity 4.0.2 has a spectrogram view, and
  the menu name for importing a label file in Audacity 4. If one of these
  differs, report what you see.
- `kizami suggest-labels` (section 4) is tested with synthetic sounds only.
  How well its candidates fit a real vocal track is **not yet measured**.

## 0. Set up (once)

Kizami needs Python 3.10 or newer.

```
git clone https://github.com/Kei-Takamizawa/Kizami.git
cd Kizami
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pip install demucs numpy
```

`numpy` is named on purpose. demucs 4.1.0 needs it but does not install it,
and fails with `No module named 'numpy'` without it. The first separation
downloads the model once.

Also install `ffmpeg` and Audacity, for example with Homebrew:
`brew install ffmpeg` and `brew install --cask audacity`.

Run the commands in the next sections from the `Kizami` folder, with the
environment active (`source .venv/bin/activate`).

## 1. Prepare the audio (once per song)

1. Put the song file in its folder, for example `eval/data/tune/song1/`.
   Git ignores `eval/data/`, because the files contain copyrighted audio and
   lyrics.

   ```
   eval/data/
     tune/song1/      song.wav  labels.txt  ref.json  separated/
     tune/song2/
     tune/song3/
     holdout/song4/
   ```

   `tune` songs are for choosing methods and parameters. The `holdout` song
   is evaluated only when a version is declared finished.

2. Convert the song to WAV. Use this one WAV file for every later step, so
   that all times share the same zero point. Different decoders can shift
   MP3 or AAC audio by tens of milliseconds.

   ```
   ffmpeg -i eval/data/tune/song1/song.m4a -ar 44100 -ac 2 eval/data/tune/song1/song.wav
   ```

3. Separate the vocal track.

   ```
   demucs --two-stems=vocals -o eval/data/tune/song1/separated eval/data/tune/song1/song.wav
   ```

   The vocal track is
   `eval/data/tune/song1/separated/htdemucs/song/vocals.wav`.

## 2. Choose the section

- One chorus of 30 to 45 seconds per song.
- Label the full-length `vocals.wav`. Do not cut the audio. The label times
  must be times in the full song.
- Choose a section with one lead voice. Sections where two singers sing
  different words at the same time are not supported yet.

## 3. Decide where chunks start

A chunk starts at a word boundary where you hear a clear new attack: the
moment you would tap in a rhythm game.

- Never start a chunk inside a word or compound. `夜空` is one unit.
- A particle or word ending that flows on from the previous word, with no
  new attack, stays in the previous chunk: `夜空を`.
- A long note is one chunk. Do not add labels inside a sustained vowel.
- The first word of each lyric line always starts a chunk.

These choices are partly a matter of taste, and precision depends on making
them the same way every time. Keep a short notes file with the cases you
found hard and what you decided.

## 4. Place the labels in Audacity

Menu names are from Audacity 4.0.2. Audacity 3 uses slightly different
names, given in brackets.

Use the manual way (4.2) for the check in section 6, and where a label or
a candidate is missing or in the wrong place.

### 4.1 With candidates

1. Make the candidates for the chorus. `--start` and `--end` are the first
   and last second of the section. Without them, the full song is marked.

   ```
   kizami suggest-labels eval/data/tune/song1/vocals.wav -o eval/data/tune/song1/candidates.txt --start 62 --end 104
   ```

   The file holds one label without text at each place where a sound
   starts. There are more candidates than chunks. That is intended.
   If too many real starts have no candidate, make the file again with a
   lower `--threshold-db`, for example `--threshold-db 1.0` (default 2.0).

2. In Audacity, open `vocals.wav` (**File > Import**), then import
   `candidates.txt` as labels [Audacity 3: File > Import > Labels]. Keep
   `candidates.txt` unchanged in the song folder.
3. For each chunk start, find its candidate. Play from just before it and
   check by ear that the sound starts there. Then click the label and type
   the chunk text.
4. Leave every other candidate without text. You do not need to delete
   them.
5. If the candidate is not at the attack, or there is none, leave it empty
   and add your own label by hand (4.2, steps 3 to 5). This is common for
   `s`, `sh`, `ts`, and `h`: the candidate can sit at the vowel, but the
   attack is the start of the hiss.
6. Export the labels (4.2, step 7).

### 4.2 By hand

1. **File > Import** and choose `vocals.wav`.
2. If the menu of the track (the `...` button next to the track name) has a
   spectrogram or multi-view option, turn it on. The spectrogram makes weak
   consonants such as `s` and `h` visible. If there is no such option, use
   the waveform and your ears, and mark doubtful positions with `?`.
3. Zoom in (**View > Zoom > Zoom in**) until 50 ms is clearly wider than the
   cursor. As a guide, about 2 seconds of audio across the window.
4. Click at the attack of a chunk. Check by ear: play from just before the
   cursor.
5. **Edit > Label > Add label** [Audacity 3: Edit > Labels > Add Label at
   Selection, Cmd+B]. The first label creates a label track. Type the chunk
   text exactly as in the lyrics and press Enter.
6. Repeat for each chunk.
7. **File > Export labels** [Audacity 3: File > Export Other > Export
   Labels]. In the dialog, enter the file name `labels`, keep the file type
   **Text file (*.txt)**, choose the song folder, tick the label track under
   **Included label tracks**, and press **Export**.

Place point labels only. Do not drag a region to mark where the chunk
ends. Only start times are a pass criterion, so end times in the reference
are not needed. If a label does have a region, its end time is stored and
reported, but it is never used to pass or fail a version.

### 4.3 Review generated timing

1. Write the generated timing as labels:

   ```
   kizami export-labels eval/data/tune/song1/generated.json -o eval/data/tune/song1/auto_labels.txt
   ```

2. In Audacity, open `vocals.wav`, then import `auto_labels.txt` as labels.
   Note the time when you start.
3. Play the song. Correct only what is wrong:
   - A label at the wrong time: move it to the attack.
   - A chunk start that has no label: add a label (4.2, steps 3 to 5).
   - A label where no new attack is heard: delete it, and add its text to
     the label before it.
   - Wrong text: fix the text.
4. Export the labels as `labels.txt` (4.2, step 7). Note the minutes that
   the review took.
5. Convert `labels.txt` (section 5), then count what you changed:

   ```
   kizami eval eval/data/tune/song1/ref.json eval/data/tune/song1/generated.json --details
   ```

Keep `generated.json` and `auto_labels.txt` unchanged, so that the
comparison stays possible.

### Where exactly is the attack

The attack is the first audible sound of the chunk, including the
consonant.

| First sound | Place the label at |
|---|---|
| Vowel (`あ`, `い`, `う`, `え`, `お`) | The start of the voiced sound. |
| `k`, `t`, `p`, `g`, `d`, `b` | The burst: the short click when the sound is released. Not the silence before it. |
| `s`, `sh`, `h`, `f`, `z`, `ts`, `ch` | The start of the hiss. It is visible as high-frequency noise in the spectrogram before the vowel. |
| `n`, `m`, `r`, `y`, `w` | The start of the voiced consonant. |

- A breath before a phrase is not part of the chunk.
- If the voice flows from the previous chunk with no gap, place the label
  where the vowel colour or the pitch changes.
- If you cannot decide the position within about 50 ms, put `?` at the
  start of the label text, for example `?を`. The chunk is stored as
  uncertain. The evaluation reports recall with and without such chunks.

### Label text

- Type the text as displayed, without readings. For `宇宙{そら}` in the
  lyrics file, type `宇宙`.
- Spaces, punctuation, and full-width or half-width differences do not
  matter.
- A kanji typing mistake makes the evaluation stop with a message that
  shows where the text differs. Fix the text in `labels.txt`. You do not
  need to place the label again.

## 5. Convert the labels

```
kizami import-labels eval/data/tune/song1/labels.txt -o eval/data/tune/song1/ref.json --title "song1" --skip-empty
```

`--skip-empty` drops the candidates that you left without text. The command
prints how many it dropped. Without this option, a label without text is
reported as an error, which is the safer setting for labels made by hand.

## 6. Check your own consistency (once)

The tolerance of ±50 ms is close to what a person can place by hand. This
check measures how repeatable your labels are. It also measures how much
the candidates pull your labels toward the detector.

1. On another day, label one chorus again in a new Audacity project, by
   hand (4.2) and **without candidates**. Do not look at the first labels.
2. Convert both label files, then compare them:

   ```
   kizami eval pass1.json pass2.json --details
   ```

3. Read `Recall` as your agreement with yourself. Missing and extra splits
   show where your chunk choices changed. `too late` and `too early` show
   where your timing changed.

If agreement is below about 95%, a perfect tool could not reach the targets
reliably either. Change the procedure or the tolerance before tuning
anything.

The report also gives the median timing difference between the two passes.
The held-out song may be labelled with candidates only if agreement is 95%
or more **and** the median difference is 10 ms or less. If not, label the
held-out song by hand (4.2).

## 7. Rules that keep the reference honest

- A reference from the review flow (4.3) is pulled toward Kizami output:
  a label that is a little off but sounds acceptable stays where it is. A
  score against such a reference is the share of boundaries that you
  accepted. It is higher than the share that is truly within ±50 ms. Say
  which kind of reference a score comes from.
- Candidates from `kizami suggest-labels` (4.1) come from the audio alone
  and know nothing about the lyrics. They carry a smaller risk of the same
  kind, because Kizami also looks for sound starts.
- For both ways:
  - Check by ear. Play from just before the label.
  - The attack as defined in section 4 decides, not the label that is
    already there.
  - Do the check in section 6 by hand, without candidates and without
    Kizami output. It is the only independent measurement.
- Do not look at Kizami output for the held-out song until a version is
  finished.
