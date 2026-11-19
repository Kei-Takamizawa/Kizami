# TTML output

`kizami ttml` writes timed lyrics as a TTML file for lyric players with word
timing, such as AMLL (Apple Music-like Lyrics). The app (`kizami app`) uses
the same code.

```
kizami ttml song/auto_chu.json            # writes song/auto_chu.ttml
kizami ttml song/tagger.json -o song.ttml # the fixes saved in the tagger
```

## Input

- A [timing file](timing-json-format.md), such as `auto_chu.json` from
  `kizami generate`, or a reference file.
- A tagger project (`tagger.json`). Chunks that have no time yet are left out.

## Options

| Option | Default | Meaning |
|---|---|---|
| `-o`, `--output` | input name with `.ttml` | File to write. |
| `--audio` | `vocals.wav` next to the input, if it is there | Vocal track (PCM WAV) used to find where the voice stops. |
| `--no-audio` | | Do not read audio. |
| `--offset-ms` | `0` | Display offset: moves every time in the file. Positive shows the lyrics later. From -10000 to 10000. |
| `--title`, `--artist`, `--album` | | Song details stored in the file. `--artist` can be given more than once. |
| `--lang` | `ja` | Language code of the lyrics. |

## What the file holds

- One `<p>` per lyric line and one `<span>` per chunk, each with its own
  `begin` and `end`. A space written between two words in the lyrics stays
  at the end of the span before it (the timing file keeps it). Two chunks of
  Latin text without a space between them get one, so that players keep the
  words apart (this also covers timing files made before spaces were kept).
- Times as `hh:mm:ss.mmm` (for example `00:01:23.456`).
- `itunes:timing="Word"`, `itunes:key` `L1`, `L2` ... on the lines, and
  `ttm:agent` `v1` (one more agent per named singer part).
- Title, artist and album as `amll:meta` (`musicName`, `artists`, `album`).
- The namespaces of the AMLL TTML format:
  `http://www.w3.org/ns/ttml`, `#metadata`, `#styling`,
  `http://music.apple.com/lyric-ttml-internal` and
  `http://www.example.com/ns/amll`.

The files were checked with AMLL's own TTML reader
(`@applemusic-like-lyrics/ttml` 1.0.1): it reads every line, span, time,
agent and title. Players other than AMLL were not tested.

## Where a chunk ends

A span needs an end. A timing file from `kizami generate` has chunk starts
only, so the end is found in the vocal track, with the same rule as the
tagger:

- The loudness of the voice is measured every 2 ms (averaged over about
  22 ms). *Quiet* means 40 dB below the loudest part of the track.
- A chunk ends at the first 80 ms of quiet that begins at least 60 ms after
  the chunk starts, but never later than the start of the next chunk.
- Ends that a file already has (for example set in the tagger) are kept.

On the four test songs, these ends were the same as the tagger's within
10 ms for 98 to 100% of 1,738 chunks. The rest are ends that the owner set by
hand in the tagger.

Without audio (`--no-audio`, or no `vocals.wav` next to the input), a chunk
ends where the next chunk of its line starts, and the last chunk of a line
ends 1 second after it starts or when the next line starts, whichever is
earlier.

## Display offset

`--offset-ms` adds the same number of milliseconds to every time in the file.
It is for players or earphones that play the sound late (use a positive
value) or early. Times below zero become zero. The timing file is not
changed.

## Not done yet

- Ruby (furigana): kanji are written as their reading in hiragana instead.
- Background vocals (`x-bg`), translations and romanization.
- LRC and ASS output.
