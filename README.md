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

The CV location accepts an absolute local path or a `file:` URL and is stored as
a `file:` URL. A browser may restrict opening local files from an
`http://localhost` page for security reasons.
