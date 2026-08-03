• # JobHunter Implementation Plan

  ## Overview

  Build a locally hosted JobHunter CMS for tracking job applications using FastAPI, Jinja templates, HTMX, and SQLite via
  Python’s built-in sqlite3.

  The system will support creating, reviewing, and updating applications; adding stage transitions; and viewing full
  history. It will not allow record deletion.

  ## Agreed decisions

  - UI: FastAPI + server-rendered Jinja templates + HTMX.
  - Database access: direct parameterised sqlite3 queries, no ORM.
  - Database: exactly two tables.
  - Creating an application automatically creates current Submitted history.
  - Adding a stage closes the former current stage and creates the next sequence number in one transaction.
  - “Submitted date” derives from the original Submitted history row.
  - “Last updated date” derives from the current stage’s effective_from.
  - Application edits alone do not change list ordering.
  - List is sorted newest current-stage effective-from first.
  - No delete routes, controls, or history editing.
  - Testing approach: regular—implement, then test each task before continuing.

  ## Data model

  applications

  - id INTEGER PRIMARY KEY AUTOINCREMENT
  - role TEXT NOT NULL
  - company TEXT NOT NULL
  - payment TEXT
  - job_url TEXT
  - cv_path TEXT
  - is_recruiter INTEGER NOT NULL DEFAULT 0 CHECK (is_recruiter IN (0, 1))
  - is_fully_remote INTEGER NOT NULL DEFAULT 0 CHECK (is_fully_remote IN (0, 1))
  - notes TEXT
  - full_jd TEXT

  submission_history

  - id INTEGER PRIMARY KEY AUTOINCREMENT
  - application_id INTEGER NOT NULL REFERENCES applications(id)
  - stage TEXT NOT NULL, restricted to Submitted, Viewed, Call, Interview, Offer, Reject, Decline, Closed
  - stage_sequence INTEGER NOT NULL
  - stage_description TEXT
  - effective_from TEXT NOT NULL
  - effective_to TEXT
  - is_current INTEGER NOT NULL CHECK (is_current IN (0, 1))

  Constraints and indexes:

  - Unique (application_id, stage_sequence).
  - Partial unique index allowing one is_current = 1 history row per application.
  - Foreign keys enabled on every SQLite connection.
  - Index supporting current-stage sorting and history retrieval.

  ## Solution structure

  app/
    main.py
    database.py
    repository.py
    templates/
      base.html
      applications/
        index.html
        form.html
        detail.html
        _application_row.html
        _stage_history.html
    static/
      app.css
  docs/
    plans/
  private/
  tests/
    conftest.py
    test_database.py
    test_repository.py
    test_routes.py
  requirements.txt
  README.md

  ## Implementation steps

  ### Task 1: Scaffold the local FastAPI application

  Files:

  - Create: requirements.txt
  - Create: app/main.py
  - Create: app/templates/base.html
  - Create: app/static/app.css
  - Create: tests/conftest.py
  - [ ] Add FastAPI, Uvicorn, Jinja2, HTMX-compatible template setup, and pytest dependencies.
  - [ ] Create the FastAPI application, static-file mounting, template configuration, and a health route.
  - [ ] Add a reusable test client fixture.
  - [ ] Write tests for app startup and the health route.
  - [ ] Run pytest successfully before Task 2.

  ### Task 2: Implement SQLite schema and connection management

  Files:

  - Create: app/database.py
  - Create: tests/test_database.py
  - [ ] Create SQLite connection helpers with foreign keys enabled.
  - [ ] Add idempotent schema initialization for the two approved tables only.
  - [ ] Add stage-value, boolean, sequence, foreign-key, and single-current-stage constraints.
  - [ ] Add necessary indexes for current-stage lookup, history ordering, and list sorting.
  - [ ] Write tests for schema creation and each database constraint.
  - [ ] Run pytest successfully before Task 3.

  ### Task 3: Add application and history repository operations

  Files:

  - Create: app/repository.py
  - Create: tests/test_repository.py
  - [ ] Implement creation of an application plus its initial current Submitted history row in one transaction.
  - [ ] Implement fetching an application with current-stage data, submitted date, and full history.
  - [ ] Implement application-field updates without changing history timestamps.
  - [ ] Implement the application-list query ordered by current effective_from descending.
  - [ ] Write tests for successful creation, rollback behaviour, lookup, update, and ordering.
  - [ ] Run pytest successfully before Task 4.

  ### Task 4: Implement transactional stage transitions

  Files:

  - Modify: app/repository.py
  - Modify: tests/test_repository.py
  - [ ] Add an operation that closes the current stage and inserts the next current stage atomically.
  - [ ] Calculate stage_sequence from the existing application history.
  - [ ] Reject a missing application, invalid stage, and an effective-from date earlier than the current stage’s start
    date.

  - [ ] Preserve existing history rows as immutable records.
  - [ ] Write success tests for stage closure, sequence increment, and current-stage replacement.
  - [ ] Write error and transaction-rollback tests.
  - [ ] Run pytest successfully before Task 5.

  ### Task 5: Build application-list and creation screens

  Files:

  - Modify: app/main.py
  - Create: app/templates/applications/index.html
  - Create: app/templates/applications/form.html
  - Create: app/templates/applications/_application_row.html
  - Modify: app/static/app.css
  - Modify: tests/test_routes.py
  - [ ] Add routes for the register page and the new-application form.
  - [ ] Render all requested list columns, including booleans, links, current-stage values, submitted date, and last
    updated date.

  - [ ] Add an HTMX-enabled create form that inserts an application and returns the refreshed row/list.
  - [ ] Keep payment, job URL, and CV location as unvalidated text input.
  - [ ] Write route tests for list rendering and successful/invalid application creation.
  - [ ] Run pytest successfully before Task 6.

  ### Task 6: Build application review and edit screens

  Files:

  - Modify: app/main.py
  - Create: app/templates/applications/detail.html
  - Modify: app/templates/applications/form.html
  - Modify: tests/test_routes.py
  - [ ] Add an application detail page showing all stored fields and full stage history.
  - [ ] Add an HTMX-enabled edit form for role, company, payment, links, flags, and notes.
  - [ ] Ensure edits retain stage history and do not change “last updated” or row order.
  - [ ] Return clear form errors for missing role or company.
  - [ ] Write route tests for viewing and updating an application.
  - [ ] Run pytest successfully before Task 7.

  ### Task 7: Build the stage-transition interface

  Files:

  - Modify: app/main.py
  - Create: app/templates/applications/_stage_history.html
  - Modify: app/templates/applications/detail.html
  - Modify: tests/test_routes.py
  - [ ] Add a stage form with the eight allowed choices, description, and effective-from time defaulting to now.
  - [ ] Submit transitions through HTMX and refresh the current-stage summary and history list.
  - [ ] Display closed stages with effective-to timestamps and clearly identify the current stage.
  - [ ] Show validation feedback without losing the user’s submitted values.
  - [ ] Write route tests for valid transitions and invalid transition inputs.
  - [ ] Run pytest successfully before Task 8.

  ### Task 8: Finish UX and local-run documentation

  Files:

  - Modify: app/static/app.css
  - Create: README.md
  - Modify: tests/test_routes.py
  - [ ] Add readable table styling, clear boolean indicators, link styling, responsive overflow handling, and accessible
    form labels.

  - [ ] Confirm there are no delete controls or delete endpoints.
  - [ ] Document local installation, database location, startup command, and test command.
  - [ ] Document the CV field as a stored local path/link and its browser limitations.
  - [ ] Add a regression test confirming delete routes are absent.
  - [ ] Run the full pytest suite successfully.

  ### Task 9: Verify acceptance criteria

  - [ ] Create an application and verify an automatic current Submitted stage.
  - [ ] Edit application fields and confirm the current-stage timestamp is unchanged.
  - [ ] Add several stages and confirm prior stages close, sequences increment, and only one stage remains current.
  - [ ] Confirm newest current-stage effective-from appears first in the register.
  - [ ] Confirm all requested fields are visible and no records can be deleted.
  - [ ] Run pytest.

  ## Post-completion

  Manually start the app with Uvicorn, create several applications, test local CV and job-posting links in the intended
  browser, and verify stage transitions visually.
