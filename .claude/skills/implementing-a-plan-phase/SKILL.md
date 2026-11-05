---
name: implementing-a-plan-phase
description: Use when starting or continuing work on any phase (Fase 0-8) of docs/PLAN.md in the image upload service, or when the user says "next phase", "do phase N", or asks to build a module listed in the plan.
---

# Implementing a Plan Phase

## Overview
The plan is already decided. A phase is a small, test-backed slice of it. Build exactly that slice, prove it with tests, and stop.

## Steps
1. Read the phase section in `docs/PLAN.md` §8, plus every ADR it touches (see the map below). Read the actual ADR text; don't work from memory.
2. List the phase's "✅ Test" bullets. They are the acceptance criteria.
3. Load the domain skill for the area (see the map).
4. Write the tests first (**REQUIRED SUB-SKILL:** superpowers:test-driven-development), then the minimum code to pass them.
5. Run `uv run pytest`. Read the output; don't assume it passed.
6. Run the self-check in `rules.md` §8.
7. Stop at the phase boundary. Report what was built, the test result, and any deviation from the docs. Don't start the next phase without the user's go-ahead.

## Phase → ADRs → skill

| Phase | Builds | ADRs | Skill |
|---|---|---|---|
| 0 | deps, `config.py`, Celery SQLite spike | 05, 14, 16 | writing-celery-tasks |
| 1 | `db.py`, `models.py` | 06, 07 | — |
| 2 | `schemas.py` | 08, 13 | adding-api-endpoints |
| 3 | validation, storage, thumbnails, sanitize, cleanup | 09, 10, 11, 17, 18 | handling-image-files, testing-the-service |
| 4 | `MultipartStreamHandler` | 02, 03, 08 | building-the-upload-stream |
| 5 | auth, lifespan, upload endpoints | 01, 12, 16, 18 | adding-api-endpoints, building-the-upload-stream |
| 6 | Celery app and task | 04-07, 14 | writing-celery-tasks |
| 7 | image CRUD | 09, 12, 13 | adding-api-endpoints |
| 8 | error handler, Swagger `responses`, README | 16, 19 | adding-api-endpoints |

## Common mistakes
- Pulling work from a later phase forward "while I'm here". Don't; it leaves later phases half-done and untested.
- Implementing a "production" item from ADR-19 or PLAN §10. Those are documentation only.
- Silently changing a decision that turned out to be hard. Stop and ask the user (`rules.md` §7).
- Phase 0: skipping the spike, or treating it as done before the progress is visible from a **separate** Python process.
