"""Local web app: song file + pasted lyrics in, TTML out. ``kizami app``.

The command starts a small web server that only this computer can reach and
opens a page in the browser. On the page you choose a song file, paste the
lyrics, and press one button. The server then, on this computer:

1. takes the vocal track out of the song with Demucs (skipped when the file
   is a vocal track already, or when the same song was separated before),
2. runs ``kizami generate`` (level chu, kanji in hiragana),
3. ends every chunk where the voice stops (:mod:`kizami.loudness`),
4. writes a TTML file (:mod:`kizami.ttml`) that the page downloads.

Each song gets a folder in the work folder (``vocals.wav``, ``lyrics.txt``,
``auto_chu.json``, the TTML file), so it can be checked by ear in the
tagger, which the page can open. When the tagger has saved fixes
(``tagger.json``), the next TTML download can use them.

Nothing is sent to the internet. See ``docs/app.md``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import traceback
import unicodedata
import webbrowser
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, quote, unquote, urlsplit

from kizami.ttml import MAX_OFFSET_MS, TtmlError, document_to_ttml, load_for_export

DEFAULT_PORT = 8766
DEFAULT_WORK_DIR = "kizami-songs"
TOKEN_HEADER = "X-Kizami-Token"
MAX_UPLOAD_BYTES = 2 * 1024**3
MAX_JSON_BYTES = 1024 * 1024
AUDIO_SUFFIXES = (".wav", ".mp3", ".flac", ".m4a", ".aac", ".aiff", ".aif", ".ogg", ".opus", ".webm", ".mp4")
VOCALS = "vocals.wav"
LYRICS = "lyrics.txt"
TIMING = "auto_chu.json"
TAGGER_PROJECT = "tagger.json"
SOURCE = "source.json"

_INDEX_PATH = Path(__file__).with_name("index.html")
_JOB_PATH = re.compile(r"^/api/jobs/([A-Za-z0-9_-]{8,64})(/ttml|/tagger)?$")
_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'"
)


class AppError(ValueError):
    """A problem to show to the user without a traceback."""


class _HttpError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def safe_name(name: str, fallback: str = "song") -> str:
    """A file or folder name made from ``name``: letters, digits, spaces, ``-`` ``_`` ``.`` only."""
    stem = unicodedata.normalize("NFC", Path(name.replace("\\", "/")).stem)
    kept = "".join(c if (c.isalnum() or c in " -_.") else "_" for c in stem).strip(" ._")
    kept = re.sub(r"_+", "_", kept)[:60].strip(" ._")
    return kept or fallback


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


@dataclass
class Upload:
    path: Path
    name: str
    sha256: str


@dataclass
class Job:
    """One run of the pipeline. Only counts and paths are reported, never lyric text."""

    id: str
    name: str  # display name of the song, from the file name
    state: str = "running"  # running, done, failed
    step: str = "starting"
    percent: int | None = None
    error: str | None = None
    folder: Path | None = None
    started: float = field(default_factory=time.monotonic)
    finished: float | None = None
    counts: dict = field(default_factory=dict)
    title: str = ""
    artist: str = ""
    offset_ms: int = 0
    tagger_url: str | None = None

    def describe(self) -> dict:
        tagger_saved = None
        if self.folder is not None and (self.folder / TAGGER_PROJECT).is_file():
            tagger_saved = time.strftime(
                "%Y-%m-%d %H:%M", time.localtime((self.folder / TAGGER_PROJECT).stat().st_mtime)
            )
        end = self.finished if self.finished is not None else time.monotonic()
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state,
            "step": self.step,
            "percent": self.percent,
            "error": self.error,
            "seconds": round(end - self.started, 1),
            "folder": str(self.folder) if self.folder else None,
            "counts": self.counts,
            "ttml_name": f"{self.name}.ttml",
            "tagger_saved": tagger_saved,
            "tagger_url": self.tagger_url,
            "offset_ms": self.offset_ms,
        }


Pipeline = Callable[[Job, Upload, dict, "App"], None]


class App:
    """State of one ``kizami app`` server: the work folder, uploads and jobs."""

    def __init__(
        self,
        work_dir: str | Path,
        model_dir: str | Path | None,
        *,
        pipeline: Pipeline | None = None,
        tagger_starter: Callable[[Path], str] | None = None,
    ) -> None:
        self.work_dir = Path(work_dir).expanduser().resolve()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.model_dir = Path(model_dir).expanduser() if model_dir else None
        self.token = secrets.token_urlsafe(24)
        self.uploads: dict[str, Upload] = {}
        self.jobs: dict[str, Job] = {}
        self.latest: str | None = None
        self._lock = threading.Lock()
        self._pipeline = pipeline or run_pipeline
        self._tagger_starter = tagger_starter or start_tagger
        self._upload_dir = Path(tempfile.mkdtemp(prefix="kizami-app-"))

    # -- setup checks ------------------------------------------------------

    def problems(self) -> list[str]:
        """What is missing for a run, in words for the user. Empty when all is in place."""
        from kizami.generate import KANA_MODEL, missing_modules
        from kizami.separate import demucs_available, ffmpeg_available

        out = []
        if self.model_dir is None:
            out.append("No model folder. Start the app with --model-dir, or set KIZAMI_MODEL_DIR.")
        elif not (self.model_dir / KANA_MODEL / "model.safetensors").is_file():
            out.append(f"The kana model is missing: {self.model_dir / KANA_MODEL}. See docs/generate.md.")
        missing = missing_modules()
        if missing:
            out.append(f'Python packages missing: {", ".join(missing)}. Run: pip install -e ".[app]"')
        if not ffmpeg_available():
            out.append("The ffmpeg program is missing. Install it, for example: brew install ffmpeg")
        if not demucs_available():
            out.append('Demucs is missing, so only vocal tracks can be used. Run: pip install -e ".[app]"')
        return out

    def describe(self) -> dict:
        return {"work_dir": str(self.work_dir), "problems": self.problems(), "latest": self.latest,
                "max_offset_ms": MAX_OFFSET_MS}  # fmt: skip

    # -- uploads and jobs --------------------------------------------------

    def save_upload(self, name: str, stream, length: int) -> str:
        suffix = Path(name).suffix.lower()
        if suffix not in AUDIO_SUFFIXES:
            raise AppError(f"'{Path(name).name}': choose an audio file ({', '.join(AUDIO_SUFFIXES)})")
        if length <= 0:
            raise AppError("the file is empty")
        upload_id = secrets.token_urlsafe(12)
        path = self._upload_dir / f"{upload_id}{suffix}"
        digest = hashlib.sha256()
        left = length
        with open(path, "wb") as out:
            while left > 0:
                block = stream.read(min(left, 1024 * 1024))
                if not block:
                    break
                out.write(block)
                digest.update(block)
                left -= len(block)
        if left:
            path.unlink(missing_ok=True)
            raise AppError("the upload was cut off")
        with self._lock:
            self.uploads[upload_id] = Upload(path, Path(name).name, digest.hexdigest())
        return upload_id

    def start_job(self, request: dict) -> Job:
        upload_id = request.get("upload")
        lyrics = request.get("lyrics")
        if not isinstance(lyrics, str) or not lyrics.strip():
            raise AppError("paste the lyrics first")
        offset = request.get("offset_ms", 0)
        if not isinstance(offset, int) or isinstance(offset, bool) or abs(offset) > MAX_OFFSET_MS:
            raise AppError(f"the display offset must be a whole number from -{MAX_OFFSET_MS} to {MAX_OFFSET_MS} ms")
        with self._lock:
            running = [j for j in self.jobs.values() if j.state == "running"]
            if running:
                raise AppError("another song is still being processed. Wait until it is done.")
            upload = self.uploads.pop(upload_id, None) if isinstance(upload_id, str) else None
            if upload is None:
                raise AppError("the song file was not received. Choose it again.")
            job = Job(id=secrets.token_urlsafe(12), name=safe_name(upload.name))
            job.title = str(request.get("title") or "").strip()[:200]
            job.artist = str(request.get("artist") or "").strip()[:200]
            job.offset_ms = offset
            self.jobs[job.id] = job
            self.latest = job.id
        options = {"lyrics": lyrics, "is_vocals": bool(request.get("is_vocals"))}
        thread = threading.Thread(target=self._run, args=(job, upload, options), daemon=True)
        thread.start()
        return job

    def _run(self, job: Job, upload: Upload, options: dict) -> None:
        try:
            self._pipeline(job, upload, options, self)
            job.state, job.step, job.percent = "done", "done", None
        except (AppError, TtmlError) as exc:
            job.state, job.error = "failed", str(exc)
        except Exception as exc:  # noqa: BLE001 - shown to the user instead of a dead page
            job.state, job.error = "failed", f"unexpected error: {exc}"
            traceback.print_exc()
        finally:
            job.finished = time.monotonic()
            upload.path.unlink(missing_ok=True)

    def job(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if job is None:
            raise AppError("no such job")
        return job

    def ttml(self, job: Job, offset_ms: int, use_tagger: bool) -> tuple[str, str]:
        """The TTML text for a finished job, and the name of the file it was saved to."""
        if job.state != "done" or job.folder is None:
            raise AppError("the TTML file is not ready yet")
        return self.write_ttml(job, offset_ms, use_tagger)

    def write_ttml(self, job: Job, offset_ms: int, use_tagger: bool) -> tuple[str, str]:
        assert job.folder is not None
        source = job.folder / (TAGGER_PROJECT if use_tagger else TIMING)
        if not source.is_file():
            raise AppError(f"{source.name} is not in the song folder")
        text = make_ttml(source, job.folder / VOCALS, offset_ms, job.title, job.artist)
        out = job.folder / f"{job.name}.ttml"
        out.write_text(text, encoding="utf-8")
        return text, out.name

    def open_tagger(self, job: Job) -> str:
        if job.state != "done" or job.folder is None:
            raise AppError("the song is not ready yet")
        if job.tagger_url is None:
            job.tagger_url = self._tagger_starter(job.folder)
        return job.tagger_url

    def close(self) -> None:
        shutil.rmtree(self._upload_dir, ignore_errors=True)


def make_ttml(source: Path, vocals: Path | None, offset_ms: int, title: str = "", artist: str = "") -> str:
    """TTML text from a timing file or tagger project, with ends from the vocal track if it is there."""
    doc = load_for_export(source)
    if vocals is not None and vocals.is_file():
        from kizami.loudness import LoudnessError, add_end_times, loudness_from_wav

        try:
            doc = add_end_times(doc, loudness_from_wav(vocals))
        except LoudnessError as exc:
            raise AppError(str(exc)) from exc
    return document_to_ttml(doc, offset_ms=offset_ms, title=title or None, artists=[artist] if artist else [])


def run_pipeline(job: Job, upload: Upload, options: dict, app: App) -> None:
    """Separate, generate, end times, TTML. Raises ``AppError`` with a message for the user."""
    from kizami.generate import GenerateError, generate
    from kizami.separate import SeparateError, convert_to_wav, separate_vocals

    if app.model_dir is None:
        raise AppError("no model folder. Start the app with --model-dir, or set KIZAMI_MODEL_DIR.")
    folder = app.work_dir / f"{job.name}-{upload.sha256[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
    job.folder = folder

    # 1. Vocal track, reused when this exact file was handled the same way before.
    is_vocals = options["is_vocals"]
    wanted = {"sha256": upload.sha256, "file": upload.name, "separated": not is_vocals}
    vocals = folder / VOCALS
    try:
        known = json.loads((folder / SOURCE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        known = None
    remade = not (vocals.is_file() and isinstance(known, dict) and known.get("sha256") == wanted["sha256"]
                  and known.get("separated") == wanted["separated"])  # fmt: skip
    if remade:
        try:
            if is_vocals:
                job.step = "converting the vocal track"
                convert_to_wav(upload.path, vocals)
            else:
                job.step = "separating the vocals (Demucs)"
                job.percent = 0

                def separated(pct: int) -> None:
                    job.percent = pct

                separate_vocals(upload.path, vocals, progress=separated)
                job.percent = None
        except SeparateError as exc:
            raise AppError(str(exc)) from exc
        (folder / SOURCE).write_text(json.dumps(wanted, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # 2. Lyrics and timing. Fixes saved in the tagger belong to the old timing
    # when the lyrics or the vocal track changed, so they are set aside.
    text = options["lyrics"].replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
    try:
        same_lyrics = (folder / LYRICS).read_text(encoding="utf-8") == text
    except (OSError, UnicodeDecodeError):
        same_lyrics = False
    project = folder / TAGGER_PROJECT
    if project.is_file() and (not same_lyrics or remade):
        os.replace(project, folder / (TAGGER_PROJECT + ".prev"))
    (folder / LYRICS).write_text(text, encoding="utf-8")
    job.step = "timing the lyrics"

    def said(message: str) -> None:
        job.step = message

    try:
        result = generate(folder, model_dir=app.model_dir, cache=app.work_dir / "_cache", progress=said)
    except GenerateError as exc:
        raise AppError(str(exc)) from exc
    level = result.levels[0]
    job.counts = {"lines": result.lines, "chunks": level.chunks, "uncertain": level.uncertain}

    # 3. End times and the TTML file.
    job.step = "writing the TTML file"
    app.write_ttml(job, job.offset_ms, use_tagger=False)


def start_tagger(folder: Path) -> str:
    """Start the tagger for a song folder on a free port, in this process. Return its address."""
    from kizami.tagger import Session, create_server, page_url

    # The tagger opens its saved work (tagger.json) when there is one, else the timing file.
    session = Session(folder, labels=TIMING)
    server = create_server(session, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return page_url(server, session)


class _Handler(BaseHTTPRequestHandler):
    server_version = "KizamiApp"
    protocol_version = "HTTP/1.1"
    app: App  # set by create_server

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - name set by the base class
        pass

    # -- helpers ---------------------------------------------------------

    def _origins(self) -> tuple[str, ...]:
        port = self.server.server_address[1]
        return (f"127.0.0.1:{port}", f"localhost:{port}")

    def _check_host(self) -> None:
        # Refuse requests that reach this server under another host name
        # (DNS rebinding) or that another web page sends (cross-site).
        if self.headers.get("Host", "") not in self._origins():
            raise _HttpError(HTTPStatus.FORBIDDEN, "unknown host")
        origin = self.headers.get("Origin")
        if origin is not None and origin not in tuple(f"http://{o}" for o in self._origins()):
            raise _HttpError(HTTPStatus.FORBIDDEN, "unknown origin")

    def _check_token(self) -> None:
        given = self.headers.get(TOKEN_HEADER, "")
        if not secrets.compare_digest(given.encode(), self.app.token.encode()):
            raise _HttpError(HTTPStatus.FORBIDDEN, "missing or wrong token")

    def _send(self, status: HTTPStatus, body: bytes, content_type: str, *, csp: bool = False,
              extra: dict | None = None) -> None:  # fmt: skip
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if csp:
            self.send_header("Content-Security-Policy", _CSP)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(HTTPStatus.OK, body, "application/json; charset=utf-8")

    def _fail(self, error: _HttpError) -> None:
        self.close_connection = True
        self._send(error.status, error.message.encode("utf-8"), "text/plain; charset=utf-8")

    def _length(self, limit: int) -> int:
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise _HttpError(HTTPStatus.LENGTH_REQUIRED, "Content-Length is required") from None
        if length < 0 or length > limit:
            raise _HttpError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "the request is too large")
        return length

    def _json_body(self) -> dict:
        data = self.rfile.read(self._length(MAX_JSON_BYTES))
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise _HttpError(HTTPStatus.BAD_REQUEST, "the request is not valid JSON") from None
        if not isinstance(parsed, dict):
            raise _HttpError(HTTPStatus.BAD_REQUEST, "the request must be a JSON object")
        return parsed

    def _job(self, job_id: str) -> Job:
        try:
            return self.app.job(job_id)
        except AppError as exc:
            raise _HttpError(HTTPStatus.NOT_FOUND, str(exc)) from None

    # -- routes ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - name set by the base class
        try:
            self._check_host()
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            if url.path in ("/", "/index.html"):
                self._send(HTTPStatus.OK, _INDEX_PATH.read_bytes(), "text/html; charset=utf-8", csp=True)
                return
            if url.path == "/api/info":
                self._check_token()
                self._send_json(self.app.describe())
                return
            m = _JOB_PATH.match(url.path)
            if m and m.group(2) is None:
                self._check_token()
                self._send_json(self._job(m.group(1)).describe())
                return
            if m and m.group(2) == "/ttml":
                self._check_token()
                job = self._job(m.group(1))
                try:
                    offset = int(query.get("offset_ms", [str(job.offset_ms)])[0])
                except ValueError:
                    raise _HttpError(HTTPStatus.BAD_REQUEST, "offset_ms must be a whole number") from None
                use_tagger = query.get("source", ["generated"])[0] == "tagger"
                try:
                    text, name = self.app.ttml(job, offset, use_tagger)
                except (AppError, TtmlError) as exc:
                    raise _HttpError(HTTPStatus.CONFLICT, str(exc)) from None
                disposition = f"attachment; filename=\"{_ascii_name(name)}\"; filename*=UTF-8''{quote(name)}"
                self._send(HTTPStatus.OK, text.encode("utf-8"), "application/ttml+xml; charset=utf-8",
                           extra={"Content-Disposition": disposition})  # fmt: skip
                return
            raise _HttpError(HTTPStatus.NOT_FOUND, "not found")
        except _HttpError as error:
            self._fail(error)

    def do_POST(self) -> None:  # noqa: N802 - name set by the base class
        try:
            self._check_host()
            self._check_token()
            url = urlsplit(self.path)
            if url.path == "/api/upload":
                name = unquote(self.headers.get("X-File-Name", ""))
                length = self._length(MAX_UPLOAD_BYTES)
                try:
                    upload_id = self.app.save_upload(name, self.rfile, length)
                except AppError as exc:
                    raise _HttpError(HTTPStatus.BAD_REQUEST, str(exc)) from None
                self._send_json({"upload": upload_id})
                return
            if url.path == "/api/jobs":
                try:
                    job = self.app.start_job(self._json_body())
                except AppError as exc:
                    raise _HttpError(HTTPStatus.CONFLICT, str(exc)) from None
                self._send_json(job.describe())
                return
            m = _JOB_PATH.match(url.path)
            if m and m.group(2) == "/tagger":
                job = self._job(m.group(1))
                try:
                    self._send_json({"url": self.app.open_tagger(job)})
                except (AppError, ValueError, OSError) as exc:
                    raise _HttpError(HTTPStatus.CONFLICT, str(exc)) from None
                return
            raise _HttpError(HTTPStatus.NOT_FOUND, "not found")
        except _HttpError as error:
            self._fail(error)


def _ascii_name(name: str) -> str:
    ascii_only = "".join(c if c.isascii() and c.isprintable() and c not in '"\\' else "_" for c in name)
    return ascii_only or "lyrics.ttml"


def create_server(app: App, port: int = 0) -> ThreadingHTTPServer:
    """Create the server on the loopback address only. ``port`` 0 picks a free port."""
    handler = type("Handler", (_Handler,), {"app": app})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    return server


def page_url(server: ThreadingHTTPServer, app: App) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}/?t={app.token}"


def serve(
    *,
    work_dir: str | Path = DEFAULT_WORK_DIR,
    model_dir: str | Path | None = None,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
) -> None:
    """Run the app until the user presses Ctrl+C."""
    try:
        app = App(work_dir, model_dir)
    except OSError as exc:
        raise AppError(f"the work folder cannot be used: {exc}") from exc
    try:
        server = create_server(app, port)
    except OSError:
        server = create_server(app, 0)  # the usual port is busy
    url = page_url(server, app)
    print("Kizami app")
    print(f"Open {url}")
    print(f"Song folders are kept in {app.work_dir}. Press Ctrl+C to stop.")
    for problem in app.problems():
        print(f"warning: {problem}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
        app.close()
