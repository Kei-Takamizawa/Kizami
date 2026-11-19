"""Local web app for tagging lyric timing by tapping: ``kizami tagger FOLDER``.

The command starts a small web server that only this computer can reach and
opens ``index.html`` in the browser. The page reads the audio, lyrics, and
label files of one song folder, and writes two files back into that folder:

- ``tagger.json``: the work in progress (lines, chunks, times).
- ``tagger_labels.txt`` (or the ``--output`` name): Audacity style labels,
  ready for ``kizami import-labels``.

Nothing is sent to the internet. See ``docs/tagger.md``.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

AUDIO_SUFFIXES = (".wav", ".mp3", ".flac", ".m4a", ".aiff", ".ogg")
PROJECT_NAME = "tagger.json"
PROJECT_FORMAT = "kizami-tagger"
LYRICS_NAME = "lyrics.txt"
DEFAULT_OUTPUT = "tagger_labels.txt"
DEFAULT_PORT = 8765
MAX_BODY_BYTES = 8 * 1024 * 1024
TOKEN_HEADER = "X-Kizami-Token"

_INDEX_PATH = Path(__file__).with_name("index.html")
_OUTPUT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.txt$")
_LABEL_LINE = re.compile(r"^\d+(\.\d+)?\t\d+(\.\d+)?\t\S.*$")
_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "connect-src 'self'; media-src blob:; img-src data:; base-uri 'none'; form-action 'none'"
)


def _is_timing_name(name: str) -> bool:
    """A JSON file other than the tagger's own: a timing file such as ``auto_chu.json``."""
    return name.lower().endswith(".json") and name != PROJECT_NAME


class TaggerError(ValueError):
    """A problem to show to the user without a traceback."""


class _HttpError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class Session:
    """One song folder that the page may read from and write to."""

    def __init__(
        self, folder: str | Path, output: str = DEFAULT_OUTPUT, labels: str | None = None
    ) -> None:
        self.folder = Path(folder).resolve()
        if not self.folder.is_dir():
            raise TaggerError(f"{folder}: not a folder")
        if not _OUTPUT_NAME.match(output) or output == LYRICS_NAME:
            raise TaggerError(
                f"--output must be a plain .txt file name other than {LYRICS_NAME}, got '{output}'"
            )
        self.output = output
        self.token = secrets.token_urlsafe(24)
        self.labels = None
        if labels is not None:
            self.labels = self.readable(labels).name
        self._lock = threading.Lock()
        self._backed_up: set[str] = set()

    def _files(self) -> list[Path]:
        return sorted(p for p in self.folder.iterdir() if p.is_file() and not p.name.startswith("."))

    def describe(self) -> dict:
        files = self._files()
        return {
            "folder": self.folder.name,
            "audio": [p.name for p in files if p.suffix.lower() in AUDIO_SUFFIXES],
            "texts": [p.name for p in files if p.suffix.lower() == ".txt" or _is_timing_name(p.name)],
            "lyrics": LYRICS_NAME if (self.folder / LYRICS_NAME).is_file() else None,
            "project": PROJECT_NAME if (self.folder / PROJECT_NAME).is_file() else None,
            "labels": self.labels,
            "output": self.output,
        }

    def readable(self, name: str) -> Path:
        """Return the path of a file the page may read, or raise ``TaggerError``."""
        if not name or "/" in name or "\\" in name or "\x00" in name or name.startswith("."):
            raise TaggerError(f"'{name}' is not a file name in the song folder")
        path = self.folder / name
        suffix = path.suffix.lower()
        if suffix not in AUDIO_SUFFIXES and suffix not in (".txt", ".json"):
            raise TaggerError(f"'{name}': this file type cannot be opened")
        # A symbolic link must not lead out of the song folder.
        if not path.is_file() or path.resolve().parent != self.folder:
            raise TaggerError(f"'{name}' was not found in the song folder")
        return path

    def _write(self, name: str, data: bytes) -> None:
        path = self.folder / name
        with self._lock:
            if path.is_symlink():
                raise TaggerError(f"'{name}' is a link, so it is not overwritten")
            # Keep the version from before this session, once.
            if name not in self._backed_up:
                if path.is_file() and path.read_bytes() != data:
                    shutil.copy2(path, path.with_name(name + ".bak"))
                self._backed_up.add(name)
            handle, temp_name = tempfile.mkstemp(dir=self.folder, prefix=".tagger-", suffix=".tmp")
            try:
                with os.fdopen(handle, "wb") as temp:
                    temp.write(data)
                os.replace(temp_name, path)
            except BaseException:
                Path(temp_name).unlink(missing_ok=True)
                raise

    def write_project(self, data: bytes) -> None:
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise TaggerError("the project is not valid JSON") from None
        if not isinstance(parsed, dict) or parsed.get("format") != PROJECT_FORMAT:
            raise TaggerError(f"the project must have format '{PROJECT_FORMAT}'")
        self._write(PROJECT_NAME, data)

    def write_labels(self, data: bytes) -> None:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise TaggerError("the labels are not UTF-8 text") from None
        for number, line in enumerate(text.splitlines(), start=1):
            if line and not _LABEL_LINE.match(line):
                raise TaggerError(f"labels line {number} is not 'start<TAB>end<TAB>text'")
        self._write(self.output, data)


class _Handler(BaseHTTPRequestHandler):
    server_version = "KizamiTagger"
    protocol_version = "HTTP/1.1"
    session: Session  # set by create_server

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
        if not secrets.compare_digest(given.encode(), self.session.token.encode()):
            raise _HttpError(HTTPStatus.FORBIDDEN, "missing or wrong token")

    def _send(self, status: HTTPStatus, body: bytes, content_type: str, csp: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if csp:
            self.send_header("Content-Security-Policy", _CSP)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(HTTPStatus.OK, body, "application/json; charset=utf-8")

    def _fail(self, error: _HttpError) -> None:
        self.close_connection = True
        self._send(error.status, error.message.encode("utf-8"), "text/plain; charset=utf-8")

    def _body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise _HttpError(HTTPStatus.LENGTH_REQUIRED, "Content-Length is required") from None
        if length < 0 or length > MAX_BODY_BYTES:
            raise _HttpError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "the request is too large")
        return self.rfile.read(length)

    # -- routes ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - name set by the base class
        try:
            self._check_host()
            url = urlsplit(self.path)
            if url.path in ("/", "/index.html"):
                self._send(
                    HTTPStatus.OK, _INDEX_PATH.read_bytes(), "text/html; charset=utf-8", csp=True
                )
                return
            if url.path == "/api/session":
                self._check_token()
                self._send_json(self.session.describe())
                return
            if url.path == "/api/file":
                self._check_token()
                name = parse_qs(url.query).get("name", [""])[0]
                try:
                    path = self.session.readable(name)
                except TaggerError as exc:
                    raise _HttpError(HTTPStatus.NOT_FOUND, str(exc)) from None
                self._send(HTTPStatus.OK, path.read_bytes(), "application/octet-stream")
                return
            raise _HttpError(HTTPStatus.NOT_FOUND, "not found")
        except _HttpError as error:
            self._fail(error)

    def do_PUT(self) -> None:  # noqa: N802 - name set by the base class
        try:
            self._check_host()
            self._check_token()
            url = urlsplit(self.path)
            if url.path not in ("/api/project", "/api/labels"):
                raise _HttpError(HTTPStatus.NOT_FOUND, "not found")
            body = self._body()
            try:
                if url.path == "/api/project":
                    self.session.write_project(body)
                else:
                    self.session.write_labels(body)
            except TaggerError as exc:
                raise _HttpError(HTTPStatus.BAD_REQUEST, str(exc)) from None
            except OSError as exc:
                raise _HttpError(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc)) from None
            self._send_json({"ok": True})
        except _HttpError as error:
            self._fail(error)


def create_server(session: Session, port: int = 0) -> ThreadingHTTPServer:
    """Create the server on the loopback address only. ``port`` 0 picks a free port."""
    handler = type("Handler", (_Handler,), {"session": session})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    return server


def page_url(server: ThreadingHTTPServer, session: Session) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}/?t={session.token}"


def serve(
    folder: str | Path,
    *,
    port: int = DEFAULT_PORT,
    output: str = DEFAULT_OUTPUT,
    labels: str | None = None,
    open_browser: bool = True,
) -> None:
    """Run the tagger for one song folder until the user presses Ctrl+C."""
    session = Session(folder, output=output, labels=labels)
    try:
        server = create_server(session, port)
    except OSError:
        # The usual port is busy, for example a second tagger is open.
        server = create_server(session, 0)
    url = page_url(server, session)
    print(f"Kizami tagger for {session.folder}")
    print(f"Open {url}")
    print(f"Labels are saved to {session.output}. Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
