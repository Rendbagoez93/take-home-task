from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

import pytest
from python_multipart.exceptions import MultipartParseError

from app.config import Settings
from app.services.multipart_stream import (
    MultipartStreamHandler,
    PartData,
    PartEnd,
    PartEvent,
    PartRejected,
)

MakeImageFile = Callable[..., Path]

BOUNDARY = "----testboundary7MA4YWxkTrZu0gW"
CONTENT_TYPE = f"multipart/form-data; boundary={BOUNDARY}"
WHOLE_BODY = 10**9
CHUNK_SIZES = pytest.mark.parametrize("chunk_size", [1, 7, WHOLE_BODY], ids=["1-byte", "7-byte", "whole"])

TEXT_CONTENT = b"hello, this is not an image"


def file_part(filename: str, content: bytes, content_type: str = "image/png") -> bytes:
    headers = (
        f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    )
    return headers.encode() + content + b"\r\n"


def field_part(name: str, value: str) -> bytes:
    return f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()


def multipart_body(*parts: bytes) -> bytes:
    delimiter = f"--{BOUNDARY}\r\n".encode()
    return b"".join(delimiter + part for part in parts) + f"--{BOUNDARY}--\r\n".encode()


def stream(body: bytes, chunk_size: int) -> list[PartEvent]:
    handler = MultipartStreamHandler(CONTENT_TYPE)
    events: list[PartEvent] = []
    for start in range(0, len(body), chunk_size):
        events += handler.feed(body[start : start + chunk_size])
    events += handler.finish()
    return events


def outcomes(events: list[PartEvent]) -> tuple[dict[str, bytes], dict[str, str]]:
    """Return ({filename: received bytes} for accepted parts, {filename: code} for rejected parts)."""
    received: defaultdict[int, bytearray] = defaultdict(bytearray)
    accepted: dict[str, bytes] = {}
    rejected: dict[str, str] = {}
    for event in events:
        match event:
            case PartData():
                received[event.index] += event.data
            case PartEnd():
                accepted[event.filename] = bytes(received[event.index])
            case PartRejected():
                rejected[event.filename] = event.code
    return accepted, rejected


def data_indexes(events: list[PartEvent]) -> set[int]:
    return {event.index for event in events if isinstance(event, PartData)}


@pytest.fixture
def png_bytes(make_image_file: MakeImageFile) -> Callable[[str], bytes]:
    def make(color: str) -> bytes:
        return make_image_file("PNG", color=color, name=f"{color}.png").read_bytes()

    return make


@pytest.fixture
def small_file_limit(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> int:
    # A real 1 MB file fed one byte at a time takes minutes; the limit itself is the subject.
    monkeypatch.setattr(settings, "max_file_bytes", 4096)
    return settings.max_file_bytes


@CHUNK_SIZES
def test_handler_accepts_three_valid_files_intact(
    chunk_size: int, png_bytes: Callable[[str], bytes]
) -> None:
    files = {"a.png": png_bytes("red"), "b.png": png_bytes("green"), "c.png": png_bytes("blue")}
    body = multipart_body(*(file_part(name, content) for name, content in files.items()))

    accepted, rejected = outcomes(stream(body, chunk_size))

    assert accepted == files
    assert rejected == {}


@CHUNK_SIZES
def test_handler_rejects_fourth_file_as_too_many_files(
    chunk_size: int, png_bytes: Callable[[str], bytes]
) -> None:
    names = ["a.png", "b.png", "c.png", "d.png"]
    body = multipart_body(*(file_part(name, png_bytes("red")) for name in names))

    accepted, rejected = outcomes(stream(body, chunk_size))

    assert sorted(accepted) == ["a.png", "b.png", "c.png"]
    assert rejected == {"d.png": "too_many_files"}


@CHUNK_SIZES
def test_handler_emits_no_data_for_files_past_the_limit(
    chunk_size: int, png_bytes: Callable[[str], bytes]
) -> None:
    names = ["a.png", "b.png", "c.png", "d.png", "e.png"]
    body = multipart_body(*(file_part(name, png_bytes("red")) for name in names))

    assert data_indexes(stream(body, chunk_size)) == {0, 1, 2}


@CHUNK_SIZES
def test_handler_rejects_oversized_middle_file_and_accepts_the_others(
    chunk_size: int, png_bytes: Callable[[str], bytes], small_file_limit: int
) -> None:
    first, last = png_bytes("red"), png_bytes("blue")
    oversized = png_bytes("green") + b"\0" * small_file_limit
    body = multipart_body(
        file_part("first.png", first), file_part("big.png", oversized), file_part("last.png", last)
    )

    accepted, rejected = outcomes(stream(body, chunk_size))

    assert accepted == {"first.png": first, "last.png": last}
    assert rejected == {"big.png": "too_large"}


@CHUNK_SIZES
def test_handler_never_emits_more_than_the_file_limit(
    chunk_size: int, png_bytes: Callable[[str], bytes], small_file_limit: int
) -> None:
    body = multipart_body(file_part("big.png", png_bytes("green") + b"\0" * small_file_limit))

    events = stream(body, chunk_size)

    emitted = sum(len(event.data) for event in events if isinstance(event, PartData))
    assert emitted <= small_file_limit


@CHUNK_SIZES
def test_handler_accepts_file_of_exactly_the_limit(
    chunk_size: int, png_bytes: Callable[[str], bytes], small_file_limit: int
) -> None:
    content = png_bytes("green")
    content += b"\0" * (small_file_limit - len(content))
    body = multipart_body(file_part("full.png", content))

    accepted, _ = outcomes(stream(body, chunk_size))

    assert accepted == {"full.png": content}


def test_handler_survives_boundary_split_across_chunks(png_bytes: Callable[[str], bytes]) -> None:
    files = {"a.png": png_bytes("red"), "b.png": png_bytes("blue")}
    body = multipart_body(*(file_part(name, content) for name, content in files.items()))
    second_boundary = body.index(f"--{BOUNDARY}".encode(), 1)
    split_at = second_boundary + len(BOUNDARY) // 2

    handler = MultipartStreamHandler(CONTENT_TYPE)
    events = handler.feed(body[:split_at]) + handler.feed(body[split_at:]) + handler.finish()

    assert outcomes(events) == (files, {})


@CHUNK_SIZES
def test_handler_ignores_non_file_fields(chunk_size: int, png_bytes: Callable[[str], bytes]) -> None:
    content = png_bytes("red")
    body = multipart_body(field_part("note", "hello"), file_part("a.png", content))

    events = stream(body, chunk_size)

    assert outcomes(events) == ({"a.png": content}, {})
    assert data_indexes(events) == {0}


def test_handler_ignores_part_with_empty_filename() -> None:
    body = multipart_body(file_part("", b"", content_type="application/octet-stream"))

    assert stream(body, WHOLE_BODY) == []


def test_handler_rejects_disallowed_extension(png_bytes: Callable[[str], bytes]) -> None:
    body = multipart_body(file_part("notes.txt", png_bytes("red")))

    assert outcomes(stream(body, WHOLE_BODY)) == ({}, {"notes.txt": "unsupported_type"})


def test_handler_rejects_disallowed_content_type(png_bytes: Callable[[str], bytes]) -> None:
    body = multipart_body(file_part("a.png", png_bytes("red"), content_type="text/plain"))

    assert outcomes(stream(body, WHOLE_BODY)) == ({}, {"a.png": "unsupported_type"})


@CHUNK_SIZES
def test_handler_rejects_wrong_magic_bytes_without_emitting_data(chunk_size: int) -> None:
    body = multipart_body(file_part("fake.png", TEXT_CONTENT))

    events = stream(body, chunk_size)

    assert outcomes(events) == ({}, {"fake.png": "unsupported_type"})
    assert data_indexes(events) == set()


def test_handler_rejects_wrong_magic_bytes_before_size(small_file_limit: int) -> None:
    body = multipart_body(file_part("fake.png", TEXT_CONTENT * small_file_limit))

    assert outcomes(stream(body, WHOLE_BODY)) == ({}, {"fake.png": "unsupported_type"})


def test_handler_rejects_empty_file() -> None:
    body = multipart_body(file_part("empty.png", b""))

    assert outcomes(stream(body, WHOLE_BODY)) == ({}, {"empty.png": "empty_file"})


@CHUNK_SIZES
def test_handler_passes_file_shorter_than_magic_header_with_valid_signature(chunk_size: int) -> None:
    body = multipart_body(file_part("tiny.gif", b"GIF89a", content_type="image/gif"))

    assert outcomes(stream(body, chunk_size)) == ({"tiny.gif": b"GIF89a"}, {})


def test_handler_rejects_file_shorter_than_magic_header_with_unknown_signature() -> None:
    body = multipart_body(file_part("tiny.png", b"abc"))

    assert outcomes(stream(body, WHOLE_BODY)) == ({}, {"tiny.png": "unsupported_type"})


@pytest.mark.parametrize(
    "raw_filename", ["C:\\fakepath\\cat.png", "../../cat.png", "/etc/cat.png", " cat.png "]
)
def test_handler_strips_directories_from_filename(
    raw_filename: str, png_bytes: Callable[[str], bytes]
) -> None:
    body = multipart_body(file_part(raw_filename, png_bytes("red")))

    accepted, _ = outcomes(stream(body, WHOLE_BODY))

    assert list(accepted) == ["cat.png"]


def test_handler_numbers_file_parts_in_order(png_bytes: Callable[[str], bytes]) -> None:
    body = multipart_body(file_part("a.png", png_bytes("red")), file_part("b.png", png_bytes("blue")))

    ends = [event for event in stream(body, WHOLE_BODY) if isinstance(event, PartEnd)]

    assert [(end.index, end.filename, end.image_format) for end in ends] == [
        (0, "a.png", "PNG"),
        (1, "b.png", "PNG"),
    ]


@pytest.mark.parametrize(
    "content_type", ["application/json", "multipart/form-data", "multipart/mixed; boundary=abc", ""]
)
def test_handler_rejects_request_without_form_data_boundary(content_type: str) -> None:
    with pytest.raises(MultipartParseError):
        MultipartStreamHandler(content_type)


def test_handler_finish_raises_when_body_ends_before_closing_boundary(
    png_bytes: Callable[[str], bytes],
) -> None:
    body = multipart_body(file_part("a.png", png_bytes("red")))
    handler = MultipartStreamHandler(CONTENT_TYPE)
    handler.feed(body[: len(body) // 2])

    with pytest.raises(MultipartParseError):
        handler.finish()
