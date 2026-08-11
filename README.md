# JobHunter

JobHunter is a local FastAPI application register for assessing jobs and
creating evidence-cited, one-page-targeted CV drafts. Generated CVs are
editable AI drafts; they require human factual, editorial, and pagination
review before PDF export or submission.

## Requirements and private setup

JobHunter requires Python 3.14 and `uv`. Sync the locked dependencies:

```bash
uv sync --locked
```

Create the ignored private profile inputs from the public examples:

```bash
mkdir -p private/profile
cp examples/profile/profile.example.md private/profile/profile.md
cp examples/profile/cv-layout.example.yaml private/profile/cv-layout.yaml
cp examples/profile/cv-template.example.docx private/profile/cv-template.docx
```

Replace the synthetic profile and template content with your own details.
The application validates these three regular files beneath `private/`,
rejects symlinks and oversized or malformed inputs, and never creates a
normalized profile copy or profile index.

The tracked AI routing and queue settings are in `config/ai.yaml`. Credentials
must not be placed in YAML; provide the OpenAI key through the process
environment:

```bash
export OPENAI_API_KEY='...'
```

`private/` is ignored by Git. The default SQLite database is
`private/jobhunter.db`; use `--database` with the launcher to select another
database path.

## Run locally

Use the packaged launcher for normal operation:

```bash
uv run jobhunter
```

It starts one FastAPI/Uvicorn process and one dispatcher under a supervising
parent. The dispatcher claims durable SQLite work and starts at most three
spawn-isolated workers. Workers open their own database, provider, settings,
private-input, and artefact resources. The launcher propagates child failures
and performs bounded cooperative, terminate, and kill cleanup.

Options include `--host`, `--port`, `--database`, and `--project-root`:

```bash
uv run jobhunter --host 127.0.0.1 --port 8000
```

Direct Uvicorn invocation is useful for web-only development, but it does not
run the dispatcher:

```bash
uv run uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>. The `/health` endpoint returns `{"status":
"ok"}` when the web process is available.

## Setup and consent

Open `/setup` before creating an application. The page revalidates the
profile, private DOCX template, layout YAML, tracked AI settings, and
`OPENAI_API_KEY`, while showing only safe readiness messages and configured
model names. It does not render private paths or profile content.

The page includes an explicit one-time acknowledgement that the private
profile may be transmitted to OpenAI. Acknowledgement is stored in the private
SQLite database and is required for every newly queued profile-bearing
assessment or CV-generation work item. Revoking it blocks new work; existing
application pages remain readable.

## Application workflow

Create an application with a role, company, and complete job description.
Setup readiness and consent are checked again at submission. The full job
description is immutable after creation; ordinary metadata and notes remain
editable. New applications begin in `Assessing`, and the create request
redirects immediately while background work runs.

The assessment worker sends bounded numbered profile and job-description
blocks to the configured OpenAI model. The application validates citations and
computes the result locally. The v1 score is supporting alignment multiplied
by mandatory coverage, with a configured threshold of 75 and deterministic
salary/location, remote-policy, authorization, clearance, and excluded-
business hard gates. A matched assessment queues CV generation automatically;
a mismatch remains visible and offers `Generate CV anyway`.

The detail page separates lifecycle from queue state and polls only while work
is queued or running. It shows the score, mandatory coverage, outcome,
analysis, gaps, model, timestamps, and safe retry guidance. Work has the
states `queued`, `running`, `succeeded`, and `failed`. Transient failures get
one retry; deterministic failures remain visible and do not incorrectly
change the application lifecycle.

Successful deterministic DOCX generation moves the application to `Ready for
review`. This state does not mean that factual, editorial, pagination, PDF,
or submission work is complete. After human review, record `Submitted`
through the stage editor; the submitted date is derived from the first real
`Submitted` transition.

## Generated artefacts and review

Application artefacts are stored privately under a safe relative directory of
the form:

```text
private/artefacts/<company>/<YYYY-MM-DD_role>/
```

Collisions receive the application ID. Assessment results and generation
outputs are written atomically in ID-specific subdirectories. Stored metadata
contains input, schema, instruction, model, and output hashes, but complete
prompts and raw provider responses are not persisted by default.

The generated candidate is named from the exactly cited profile identity and
the application role:

```text
<First Name> <Last Name> - <Job Title>.docx
```

The CV content and DOCX are evidence-cited drafts. Open the editable DOCX
from the application artefact directory, check every claim and disclosure
boundary against the cited profile blocks, make any required edits, and
confirm that it fits one A4 portrait page in the preferred editor. Then
manually export the final PDF and record `Submitted`. JobHunter does not
render DOCX files, count PDF pages, or automate desktop applications in v1.

On macOS, `Open artefacts` uses a POST-only, same-origin action and passes the
persisted, containment-checked directory to Finder's `open` command as an
argument array. The old CV upload, preview, and download controls are not
part of this workflow.

## Timing, recovery, and diagnostics

The tracked queue timing and concurrency defaults in `config/ai.yaml` are:

- maximum three worker processes;
- one-second idle dispatcher polling;
- ten-second worker heartbeats;
- 45-second work leases.

The durable work repository allows two attempts total, including the initial
attempt.

The dispatcher recovers expired leases, records redacted process/provider
failures, and resumes valid assessment or CV-content checkpoints only when all
relevant input hashes still match. A changed profile, job description, role,
template, layout, prompt, schema, or assessment invalidates the applicable
checkpoint.

Useful checks from the project root are:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests scripts
uv run python scripts/generate_ai_schemas.py --check
uv lock --check
git diff --check
```

There is no browser E2E suite or live OpenAI call in the automated tests.
Manual browser, credential, restart/burst, editor pagination, and PDF-export
checks are documented in the
[implementation plan's Post-Completion section](docs/plans/completed/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md#post-completion).
