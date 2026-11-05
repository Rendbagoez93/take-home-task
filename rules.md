# Project Rules

These rules apply to every file in this repo. The decisions in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (ADR-01 to ADR-19) are binding. [docs/PLAN.md](docs/PLAN.md) defines the scope. These rules govern *how* the code is written.

Priority when rules pull against each other: **correct > readable > simple > short.**

---

## 1. Readability first

- Write the version a new reader understands in one pass, even if it is a few lines longer.
- Use the names the plan already defines: model columns (PLAN §5), function names (PLAN §4, §8), error codes (`too_large`, `unsupported_type`, `invalid_image`, `too_many_files`, `empty_file`). Do not invent synonyms.
- Names say what the thing is: `bytes_received`, `stored_filename`, `make_thumbnails`. Abbreviate only universal terms (`db`, `id`, `url`, `ext`).
- One function does one thing. Aim for 30 lines or fewer. A longer function is fine only when the steps are a strict sequence that splitting would hide (for example, the ADR-09 order).
- Prefer early returns to nested `if`. Keep nesting at 3 levels or fewer.
- Type hints on every function signature. Use modern syntax: `list[str]`, `str | None`.
- Keep to the module layout in PLAN §4. Add a new module only when no existing module fits.
- No magic numbers. Limits and sizes (1 MB, 3 files, 600 s, 128/256/512, 64 KB) live in `config.Settings`.

## 2. Simplicity

- Write the simplest code that satisfies the current PLAN phase and its tests.
- Plain functions by default. Use a class only when it holds state across calls: `MultipartStreamHandler`, SQLAlchemy models, Pydantic schemas, `Settings`.
- Use the framework directly: `Depends`, `HTTPException`, Pydantic `Field(...)` constraints, SQLAlchemy `select()`. Do not wrap them in your own helpers.
- Use sync code by default. `async def` is only for the streaming upload endpoint (ADR-12).
- Be explicit, not clever: no metaprogramming, no dynamic imports, no `**kwargs` pass-through, no one-line tricks that need a second read.
- Use the standard library and the agreed stack (PLAN §3). **Ask before adding any dependency.**

## 3. No over-engineering

Do not build:

- Abstract base classes, `Protocol`s, or interfaces with one implementation. `storage.py` is a module of plain functions, and that module *is* the seam (ADR-13). No `StorageBackend` ABC.
- Repository, unit-of-work, service-locator, or DI-container layers on top of the SQLAlchemy session.
- Helpers "for later". A helper needs 2 or more real callers, or it must make its one call site clearly easier to read.
- Settings that nothing varies, feature flags, plugin systems, event buses, or deep custom exception hierarchies.
- Retries, caches, or fallbacks that no ADR asks for. The only retry is `autoretry_for=(OperationalError,)` on the Celery task (ADR-06).
- Anything from ADR-19 or PLAN §10 (rate limiting, CORS, S3, Alembic, SSE/WebSocket, ownership, Docker). Those items are documented, not implemented.

Before writing something, answer: **"Which test or ADR requires this?"** If nothing does, leave it out.

## 4. Comments and docstrings

- **Default: no comment.** Good names and small functions already say *what* the code does.
- Comment only the *why*: a non-obvious constraint, an ordering requirement, or a workaround. Cite the ADR when there is one.
- No docstring that repeats the function name or signature. Add a one-line docstring only when the behavior is not obvious from the name and types.
- No banner or section-divider comments, no commented-out code, no change-log comments ("added X", "fixed Y"), no `TODO` without a reason.
- Do not comment test bodies line by line. The test name describes the case.

```python
# Bad
def promote(temp_path: Path, ext: str) -> str:
    """Promote a temp file.

    Args:
        temp_path: the temp path
        ext: the extension
    Returns:
        the stored name
    """
    # generate a new uuid name
    stored_name = f"{uuid4()}.{ext}"
    # move the file
    os.replace(temp_path, settings.originals_dir / stored_name)
    # return the name
    return stored_name

# Good
def promote(temp_path: Path, ext: str) -> str:
    stored_name = f"{uuid4()}.{ext}"
    os.replace(temp_path, settings.originals_dir / stored_name)  # atomic: no half-written finals (ADR-09)
    return stored_name
```

## 5. Error handling

- Handle the errors the plan lists: the HTTP statuses in PLAN §6 and the per-file rejection codes. Let everything else reach the global exception handler.
- Catch the narrowest exception type. Never use a bare `except:` or `except Exception: pass`.
- Use `try/finally` for cleanup (temp files, `Upload.status = failed`), as ADR-09 requires.
- A file that is already missing during a delete is not an error (`missing_ok=True`).

## 6. Tests

- Use plain `pytest` functions with shared fixtures in `tests/conftest.py`. No test-class hierarchies.
- One behavior per test. Name tests `test_<subject>_<expected_result>`.
- Generate test images with Pillow inside fixtures. Never commit binary files.
- A phase is done only when `uv run pytest` is green.

## 7. Stay aligned with the docs

- ADRs are decisions already made. Follow them in code; do not re-debate them.
- If the implementation shows that an ADR or the plan is wrong or impossible, **stop and tell the user** before deviating. Update the docs only after the user agrees.
- Write code, identifiers, comments, and commit messages in English. The docs stay in Indonesian.

## 8. Self-check before calling work done

- [ ] Every new function, class, or setting is required by a test or an ADR.
- [ ] No comment explains *what*; every remaining comment explains *why*.
- [ ] No new abstraction has only one implementation.
- [ ] No blocking call inside an `async def` without `anyio.to_thread.run_sync`.
- [ ] No magic numbers; limits come from `Settings`.
- [ ] `uv run pytest` is green.
