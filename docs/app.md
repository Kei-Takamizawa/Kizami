# The Kizami app

`kizami app` opens a page in your browser. You choose a song file, paste the
lyrics, and press **Make TTML**. A few minutes later you download a TTML file
that lyric players such as AMLL can show with the timing of the voice.

Everything runs on your computer. The song and the lyrics are not sent
anywhere.

## Set up once

1. Install Kizami with everything the app needs (Python 3.10 or newer):

   ```
   git clone https://github.com/Kei-Takamizawa/Kizami.git
   cd Kizami
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e ".[app]"
   ```

2. Install the `ffmpeg` program, for example with `brew install ffmpeg`.

3. Download the timing model into a model folder. See
   [Generating timing](generate.md#models).

Demucs, which takes the voice out of the song, downloads its own model the
first time it runs. After that, the app works without the internet.

## Start the app

```
kizami app --model-dir path/to/models
```

The browser opens the page. (Or set the environment variable
`KIZAMI_MODEL_DIR` to the model folder and run `kizami app`.) If something is
missing, the page says what and how to install it. Press Ctrl+C in the
terminal to stop the app.

| Option | Default | Meaning |
|---|---|---|
| `--model-dir` | `$KIZAMI_MODEL_DIR` | Folder with the timing model. |
| `--work-dir` | `./kizami-songs` | Where the app keeps one folder per song. |
| `--port` | 8766 | Port of the page on this computer. Another free port is used if it is busy. |
| `--no-browser` | | Print the page address but do not open the browser. |

## Make a TTML file

1. **Song file.** Drop the song on the page or click to choose it. Most audio
   formats work (WAV, MP3, M4A, FLAC, Opus and more). If the file is a vocal
   track already, tick *This file is a vocal track already*.
2. **Lyrics.** Paste the lyrics, one lyric line per line. Blank lines and
   section tags such as `[Chorus]` are skipped. When a word is sung
   differently from how it is written, give the reading in braces:
   `明日{あした}`.
3. **Display offset.** Leave it at 0 at first. See below.
4. Press **Make TTML**. The page shows each step. Timing a four-minute song
   takes about 40 seconds on an Apple silicon Mac, plus the vocal separation.
   The same song file is separated only once, so a second run (for example
   with corrected lyrics) is faster.
5. Press **Download TTML**.

Kanji are shown as their reading in hiragana, and a new piece of text starts
at each word, at each strong attack of the voice, and after a pause or long
note of one second or more. See [Generating timing](generate.md) for how this is decided.

## Check and fix by ear

Press **Check in the tagger**. The tagger opens in a new tab with the song's
timing. Play it, and fix any piece that is off (see the
[tagger guide](tagger.md)). The tagger saves your fixes in the song folder.

Back on the app page, the result now shows *Use the fixes saved in the
tagger*. Leave it ticked and press **Download TTML** again: the new file has
your fixes.

## Display offset

Some players and some earphones (Bluetooth in particular) play the sound a
little late. Then the lyrics seem early. The display offset moves every time
in the TTML file by the same number of milliseconds:

- **Positive** (for example `150`): the lyrics appear later.
- **Negative** (for example `-50`): the lyrics appear earlier.

The offset changes only the downloaded file, never the timing in the song
folder. Change it and press **Download TTML** again to try another value; no
new run is needed. The page remembers the last value in this browser.

Without an offset, each piece of text already starts a little before the
voice (about 0.1 s on average), at the end of the previous sound. Studies of
how people judge sound and picture as in time suggest that text a little
early feels more in time than text a little late.

## The song folder

The app keeps one folder per song in the work folder, named after the song
file. It holds:

| File | What it is |
|---|---|
| `vocals.wav` | The voice, taken out of the song. Kept so the song is separated only once. |
| `lyrics.txt` | The lyrics you pasted. |
| `auto_chu.json` | The timing that Kizami made ([format](timing-json-format.md)). |
| `<song>.ttml` | The last TTML file you downloaded. |
| `tagger.json`, `tagger_labels.txt` | Your fixes, once you save them in the tagger. |
| `source.json` | Which song file the vocal track came from. |

When you run the same song again with changed lyrics, earlier fixes no longer
match, so `tagger.json` is renamed to `tagger.json.prev`.

The work folder holds lyrics and audio. Do not share it or commit it to Git
(`kizami-songs/` is in `.gitignore`).

## The same from the command line

```
kizami generate path/to/song-folder --model-dir path/to/models
kizami ttml path/to/song-folder/auto_chu.json
```

See [TTML output](ttml.md).
