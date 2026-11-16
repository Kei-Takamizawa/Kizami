# Tagger guide

The tagger is a window in your browser for tagging lyric timing by tapping.
The song plays, the lyrics are shown line by line, and you press a key at the
moment each chunk of the lyrics is sung. Afterwards you can move the start
and the end of every chunk on the waveform.

Nothing leaves your computer. The tagger reads and writes files in one song
folder only.

## Start

```
kizami tagger eval/data/tune/song1
```

The song folder needs:

- an audio file. `vocals.wav` is used first if it is there. You can switch to
  another audio file in the folder (for example the full mix) with **Audio**.
- `lyrics.txt`: the lyrics as sung, one display line per line.

Options:

| Option | Meaning |
|---|---|
| `--labels NAME` | Start from a label file in the folder, for example `auto_labels.txt`. Ignored when saved work exists. |
| `--output NAME` | Label file to write. Default `tagger_labels.txt`. |
| `--port N` | Port on this computer. Default 8765. Another free port is used if it is busy. |
| `--no-browser` | Print the address and do not open the browser. |

Stop the tagger with Ctrl+C in the terminal.

You can also open `src/kizami/tagger/index.html` directly in a browser
without the command. Then you drop the audio and text files on the page, and
**Save** downloads the result instead of writing it to the song folder.

## How lyrics become chunks

- Japanese and Korean text: one chunk per character.
- Small kana, `っ`, and `ー` stay with the character before them (`きゃ`,
  `だっ`, `ラー`).
- A Latin word or a number is one chunk.
- Punctuation stays with the chunk next to it. A space only separates chunks.
- A reading such as `宇宙{そら}` makes one chunk.
- A part name such as `[ボーカルA]` at the start of a line is kept as the
  part of the following lines.

Use **Merge** (`M`) and **Split** (`T`) to change chunks while you work.

## Tagging

1. Press `Space` to play.
2. Press `F` or `J` (also `D`, `K`) at the start of each chunk. You can use
   two fingers in turn for fast parts. A click on the **TAP** pad does the
   same.
3. A quick tap sets only the start. The chunk then ends where the next chunk
   starts, or where the sound stops if that comes first.
4. Hold the key to set the length yourself: the chunk ends when you release
   the key.

Useful while tagging:

- **Speed**: the slider, or `1` `2` `3` for 0.5×, 0.75×, 1×, and `-` `=` for
  small steps. The pitch does not change.
- `Backspace` takes back the last tap. `Enter` replays from the start of the
  line.
- To redo a part, click its first chunk, go back a little (`←`), play, and
  tap again. New taps replace the old times.
- **Snap**: a tap is moved to the nearest point where the sound starts to
  rise, if one is within the given distance (default ±60 ms). That point is
  the bottom of the dip between two sounds, or the first sound after a
  silence. Set it to 0 to keep the raw tap times.
- **Tap offset**: added to every tap, in real time. Use a negative value if
  your taps are late, for example with wireless headphones.
- **Click sound**: plays a short click at every chunk start, to check the
  result by ear.

## Changing starts and lengths

- On the timeline, drag the left edge of a chunk to move its start, the right
  edge to change its length, or the box to move the whole chunk.
- When two chunks touch, dragging the border moves both. Hold `Alt` to move
  one side only.
- `Z` `X` move the start 10 ms earlier or later. `C` `V` do the same for the
  end. With `Shift` the step is 50 ms, with `Alt` 1 ms.
- The **Start**, **End**, and **Length** fields take exact values.
- `G` snaps the start of the selected chunk to the sound.
- `R` or a double click plays only the selected chunk.
- `U` marks a chunk as unsure. It is saved with a `?` in front, as in the
  [labelling guide](labeling-guide.md). `N` jumps to the next unsure chunk.
- `⌘Z` and `⇧⌘Z` undo and redo. Zoom with a pinch, `⌘`-scroll, or the Zoom
  buttons.

All keys are listed under **Keys** at the bottom of the window.

## Files

The tagger saves by itself a moment after every change, and when you press
**Save** (`⌘S`).

| File | Content |
|---|---|
| `tagger_labels.txt` | Audacity style labels: `start<TAB>end<TAB>text`, in seconds. Read it with `kizami import-labels`. |
| `tagger.json` | The work in progress, with lines, chunks without a time, and the raw tap times. Opened again the next time you start the tagger for this folder. |
| `*.bak` | The version of a file from before the current session. One per file. |

**Load** replaces the lyrics with `lyrics.txt`, or puts the times of a label
file on the lyrics. The label text must appear in the lyrics in the same
order. If it does not, the labels are shown alone. Undo brings the earlier
state back.

These files contain lyric text. Keep them out of Git, as described in the
[timing JSON format](timing-json-format.md).

## Safety

- The server listens on `127.0.0.1` only, so other computers cannot reach it.
- Every request for data needs a secret that is part of the address printed
  at the start. Other web pages cannot read or change your files.
- The tagger reads audio and `.txt` files directly inside the song folder,
  and writes only `tagger.json`, the output label file, and their `.bak`
  copies.

## Limits

- Tap timing depends on the browser and the audio device. Use Snap, or check
  with the click sound.
- Snap uses a simple loudness curve. It can pick a wrong point when the
  accompaniment is loud, so prefer the separated vocal track.
- Times made with Snap are not independent of an automatic method that also
  uses sound starts. Set Snap to 0 for reference data that must be
  independent.
