from dataclasses import dataclass, field
from pathlib import PureWindowsPath

from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import MultipartParser, parse_options_header

from app.config import settings
from app.models import RejectionCode
from app.services.validation import MAGIC_HEAD_BYTES, check_content_type, check_extension, sniff_magic


@dataclass
class PartData:
    index: int
    data: bytes


@dataclass
class PartEnd:
    index: int
    filename: str
    image_format: str  # the format the extension claims; Pillow must detect the same one


@dataclass
class PartRejected:
    index: int
    filename: str
    code: RejectionCode
    reason: str


PartEvent = PartData | PartEnd | PartRejected


@dataclass
class PartState:
    index: int
    filename: str
    declared_type: str
    image_format: str = ""
    size_bytes: int = 0
    # Bytes held back until the magic-byte check passes; None once it has (ADR-10).
    head: bytearray | None = field(default_factory=bytearray)
    rejected_code: RejectionCode | None = None


class MultipartStreamHandler:
    """Parses a multipart body chunk by chunk and turns each file part into events.

    Sync and free of I/O so it can be tested with any chunking; the caller writes
    PartData to a temp file, and deletes it on PartRejected (ADR-02).
    """

    def __init__(self, content_type: str) -> None:
        self._parser = MultipartParser(
            form_data_boundary(content_type),
            callbacks={
                "on_part_begin": self._on_part_begin,
                "on_header_field": self._on_header_field,
                "on_header_value": self._on_header_value,
                "on_header_end": self._on_header_end,
                "on_headers_finished": self._on_headers_finished,
                "on_part_data": self._on_part_data,
                "on_part_end": self._on_part_end,
                "on_end": self._on_end,
            },
        )
        self._events: list[PartEvent] = []
        self._file_count = 0
        self._part: PartState | None = None
        self._headers: dict[bytes, bytes] = {}
        self._header_field = bytearray()
        self._header_value = bytearray()
        self._reached_end = False

    def feed(self, chunk: bytes) -> list[PartEvent]:
        self._parser.write(chunk)
        return self._take_events()

    def finish(self) -> list[PartEvent]:
        self._parser.finalize()
        if not self._reached_end:
            raise MultipartParseError("Request body ended before the closing boundary")
        return self._take_events()

    def _take_events(self) -> list[PartEvent]:
        events, self._events = self._events, []
        return events

    def _on_part_begin(self) -> None:
        self._part = None
        self._headers = {}

    # Header names and values can arrive split across chunks, so they are collected
    # until the parser reports the end of each header.
    def _on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._header_field += data[start:end]

    def _on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._header_value += data[start:end]

    def _on_header_end(self) -> None:
        self._headers[bytes(self._header_field).strip().lower()] = bytes(self._header_value).strip()
        self._header_field.clear()
        self._header_value.clear()

    def _on_headers_finished(self) -> None:
        _, disposition = parse_options_header(self._headers.get(b"content-disposition"))
        filename = display_filename(disposition.get(b"filename", b"").decode("utf-8", "replace"))
        if not filename:
            return  # a plain form field, not a file (PLAN §7)
        declared_type = self._headers.get(b"content-type", b"").decode("latin-1")
        self._part = PartState(self._file_count, filename, declared_type)
        self._file_count += 1
        self._check_headers(self._part)

    def _check_headers(self, part: PartState) -> None:
        if part.index >= settings.max_files_per_request:
            limit = settings.max_files_per_request
            self._reject(part, "too_many_files", f"Only {limit} files are allowed per request")
            return
        image_format = check_extension(part.filename)
        if image_format is None:
            suffix = PureWindowsPath(part.filename).suffix or "(none)"
            self._reject(part, "unsupported_type", f"Extension {suffix} is not allowed")
            return
        if not check_content_type(part.declared_type):
            self._reject(part, "unsupported_type", f"Content-Type {part.declared_type!r} is not allowed")
            return
        part.image_format = image_format

    def _on_part_data(self, data: bytes, start: int, end: int) -> None:
        part = self._part
        if part is None or part.rejected_code is not None:
            return
        chunk = data[start:end]
        part.size_bytes += len(chunk)
        if part.head is not None:
            part.head += chunk
            # The magic check runs before the size check whatever the chunking, so a
            # given file always gets the same rejection code (ADR-10 order).
            if len(part.head) < MAGIC_HEAD_BYTES or not self._passes_magic_check(part):
                return
            chunk, part.head = bytes(part.head), None
        if part.size_bytes > settings.max_file_bytes:
            limit = settings.max_file_bytes
            self._reject(part, "too_large", f"File is larger than the {limit} byte limit")
            return
        self._events.append(PartData(part.index, chunk))

    def _on_part_end(self) -> None:
        part, self._part = self._part, None
        if part is None or part.rejected_code is not None:
            return
        if part.size_bytes == 0:
            self._reject(part, "empty_file", "File is empty")
            return
        if part.head is not None:  # the whole file is shorter than MAGIC_HEAD_BYTES
            if not self._passes_magic_check(part):
                return
            self._events.append(PartData(part.index, bytes(part.head)))
        self._events.append(PartEnd(part.index, part.filename, part.image_format))

    def _on_end(self) -> None:
        self._reached_end = True

    def _passes_magic_check(self, part: PartState) -> bool:
        if sniff_magic(bytes(part.head or b"")) is not None:
            return True
        self._reject(part, "unsupported_type", "File content is not a supported image type")
        return False

    def _reject(self, part: PartState, code: RejectionCode, reason: str) -> None:
        part.rejected_code = code
        self._events.append(PartRejected(part.index, part.filename, code, reason))


def form_data_boundary(content_type: str) -> bytes:
    media_type, options = parse_options_header(content_type)
    boundary = options.get(b"boundary")
    if media_type != b"multipart/form-data" or not boundary:
        raise MultipartParseError("Content-Type must be multipart/form-data with a boundary")
    return boundary


def display_filename(raw_filename: str) -> str:
    # Clients may send a full path ("C:\fakepath\cat.png"); keep only the last part.
    # The name is for display only: files are stored under a UUID (ADR-10).
    return PureWindowsPath(raw_filename).name.strip()
