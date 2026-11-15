# Kizami timing JSON (version 1)

One file holds the chunk timing of one song or one excerpt of a song. The
same format is used for hand-made reference timing and for generated timing,
so the two can be compared directly.

## Example

```json
{
  "format": "kizami-timing",
  "version": 1,
  "kind": "reference",
  "meta": { "title": "Demo", "source": "audacity-labels" },
  "lines": [
    {
      "agent": "A",
      "chunks": [
        { "text": "あの", "start_ms": 12346 },
        { "text": "空を", "start_ms": 12801, "end_ms": 13400 },
        { "text": "見上げて", "start_ms": 13620, "uncertain": true }
      ]
    }
  ]
}
```

## Fields

| Field | Type | Required | Meaning |
|---|---|---|---|
| `format` | string | yes | Always `"kizami-timing"`. |
| `version` | integer | yes | Always `1`. |
| `kind` | string | yes | `"reference"` (made by a person) or `"generated"` (made by Kizami). |
| `meta` | object | no | Free information such as `title`. Not used by evaluation. |
| `lines` | list | yes | Lyric lines in song order. At least one. |
| `lines[].agent` | string | no | Singer part name. |
| `lines[].chunks` | list | yes | Chunks in sung order. At least one. |
| `chunks[].text` | string | yes | The lyric text of the chunk as displayed. Not empty. |
| `chunks[].start_ms` | integer | yes | Attack time in milliseconds from the start of the audio file. 0 or more. |
| `chunks[].end_ms` | integer | no | Time when the sound of the chunk ends. Omitted when unknown. |
| `chunks[].uncertain` | boolean | no | `true` when the labeller could not place the attack with confidence. Default `false`. |

## Rules

- **Time base.** All times are measured from the start of the same audio
  file. Use one WAV file for separation, labelling, and generation.
- **Attack.** `start_ms` is the first audible sound of the chunk, including
  its consonant.
- **Reference files from Audacity** have one line that holds every chunk.
  Line breaks do not affect evaluation.
- **Structure only.** Loading a file checks types and required fields. It
  does not reject timing problems such as an end before a start. The
  evaluation counts those and reports them.
- **Copyright.** A timing file contains lyric text. Do not commit timing
  files of copyrighted songs. Keep them in `eval/data/`, which Git ignores.

## Compatibility

Readers must ignore fields they do not know. New optional fields may be
added without a version change. A change that breaks old readers increases
`version`.
