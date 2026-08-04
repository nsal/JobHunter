# JobHunter

JobHunter is a local FastAPI application register with immutable stage history.

## Run locally

JobHunter requires Python 3.14. Sync the locked dependencies, then start the
development server:

```bash
uv sync --locked
uv run --locked uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>. The SQLite database is stored at
`private/jobhunter.db` by default. Set `JOBHUNTER_DATABASE` to use another path.

Run the checks with:

```bash
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy app
```

The job URL is optional, for example when the job description arrived by email.
When supplied, it must be an absolute `http` or `https` URL.

JobHunter keeps CVs in their original location; it does not upload or copy
them. Set `JOBHUNTER_CV_ROOT` to the directory from which CVs may be selected.
When it is not set, the current user's home directory is used. The picker
accepts PDF, DOC, and DOCX files and stores a validated `file:` URL. PDFs open
in an in-app preview; Word files use the operating system's normal
download/open flow.

The dashboard search matches role, company, and notes. Its notes and job
description previews are limited to 300 characters, and Submitted and Last
updated summary fields display dates without times.
