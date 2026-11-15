# How to make reference timing

Reference timing is the ground truth that Kizami output is measured against.
A person makes it in Audacity by placing one label at the attack of each
chunk. This guide makes the result repeatable.

The shell commands for audio conversion and vocal separation in section 1
were written from tool documentation. They were **not yet run** as part of
this project. If one fails, report the error message.

## 1. Prepare the audio (once per song)

1. Convert the song to WAV. Use this one WAV file for every later step, so
   that all times share the same zero point. Different decoders can shift
   MP3 or AAC audio by tens of milliseconds.

   ```
   ffmpeg -i song.mp3 -ar 44100 -ac 2 song.wav
   ```

2. Separate the vocal track.

   ```
   python3 -m pip install demucs
   demucs --two-stems=vocals -o separated song.wav
   ```

   The vocal track is `separated/htdemucs/song/vocals.wav`.

3. Keep the files in `eval/data/`. Git ignores this folder, because the
   files contain copyrighted audio and lyrics.

   ```
   eval/data/
     tune/song1/      song.wav  vocals.wav  labels.txt  ref.json
     tune/song2/
     tune/song3/
     holdout/song4/
   ```

   `tune` songs are for choosing methods and parameters. The `holdout` song
   is evaluated only when a version is declared finished.

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

Menu names are from Audacity 3 and can differ slightly between versions.

1. **File > Import > Audio** and choose `vocals.wav`.
2. In the track menu, choose **Multi-view** to see the waveform and the
   spectrogram together.
3. Zoom in until 50 ms is clearly wider than the cursor. As a guide, about
   2 seconds of audio across the window.
4. Click at the attack of a chunk. Check by ear: play from just before the
   cursor.
5. Press **Cmd+B** (Edit > Labels > Add Label at Selection), type the chunk
   text exactly as in the lyrics, and press Enter.
6. Repeat for each chunk.
7. **File > Export Other > Export Labels** and save as `labels.txt`.

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
