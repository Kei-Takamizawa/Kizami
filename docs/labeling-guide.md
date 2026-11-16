# How to make reference timing

Reference timing is the ground truth that Kizami output is measured against.
A person makes it in Audacity by placing one label at the attack of each
chunk. This guide makes the result repeatable.

What was checked on a real machine (2026-11-15, macOS on Apple Silicon,
Python 3.14, demucs 4.1.0, torch 2.15.0, ffmpeg 9.0.2, Audacity 4.0.2):

- The setup commands in section 0 and the `demucs` command in section 1 ran
  without error. The input was a 10-second test tone. The `ffmpeg`
  conversion and the separation were **not yet run on a real song**.
- The Audacity menu names in section 4 were read from Audacity 4.0.2.
- **Not yet checked:** the content of a label file exported by Audacity 4,
  the Cmd+B shortcut, and whether Audacity 4.0.2 has a spectrogram view.
  If one of these differs, report what you see.

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

A label at one point is enough. If you drag a region from the attack to the
end of the sound, the end time is stored too. End times are optional.

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
kizami import-labels eval/data/tune/song1/labels.txt -o eval/data/tune/song1/ref.json --title "song1"
```

## 6. Check your own consistency (once)

The tolerance of ±50 ms is close to what a person can place by hand. This
check measures how repeatable your labels are.

1. On another day, label one chorus again in a new Audacity project. Do not
   look at the first labels.
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

## 7. Rules that keep the reference honest

- Do not make reference timing by correcting Kizami output. Start from an
  empty label track.
- Do not look at Kizami output for the held-out song until a version is
  finished.
