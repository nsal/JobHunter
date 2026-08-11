# AI assessment and tailored CV generation roadmap

## Purpose

Build JobHunter toward a private, evidence-grounded workflow that assesses a
job description against a user's career profile and produces a tailored CV.
Deliver the capability in independently useful stages instead of treating
provider support, profile indexing, document fitting, and operational
hardening as one implementation unit.

The first stage is specified separately in
`docs/plans/completed/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`.

## Guiding decisions

- Keep FastAPI responsive by running AI and document-generation work outside
  the web process.
- Use a supervised dispatcher and a durable SQLite queue for asynchronous
  execution.
- Keep private profiles, templates, settings, consent state, and generated
  artefacts under the ignored `private/` tree.
- Require structured model output, cited profile/job-description evidence, and
  deterministic application-side scoring.
- Keep lifecycle stage separate from work status.
- Generate an editable DOCX targeted at one A4 page, then preserve factual,
  editorial, pagination review, and final PDF export as user responsibilities.
- Add provider and workflow complexity only when a completed earlier stage
  demonstrates the need.

## Stage 1: OpenAI assessment-to-DOCX vertical slice

Deliver one complete workflow:

```text
Application + immutable JD + current profile.md
                    |
                    v
          queued assessment worker
                    |
                    v
   structured requirements/evidence result
                    |
                    v
          deterministic scoring
             |              |
          mismatch        matched
             |              |
      optional override      v
             +------> queued CV worker
                              |
                              v
                    grounded CvContent
                              |
                              v
                  private template + YAML
                              |
                              v
                    Ready for review
                              |
                 manual pagination and PDF export
```

Scope:

- Rebuild the development SQLite schema directly; no migration framework or
  legacy-data conversion is required before production data exists.
- Remove the old CV upload, preview, download, and storage implementation.
- Add `Assessing`, `Mismatch`, and `Ready for review`; record `Submitted` only
  when the user explicitly applies.
- Read the current private Markdown profile when work starts. Record its hash,
  but do not build an index, require profile approval, or retain a snapshot.
- Require one-time acknowledgement before sending profile content to OpenAI.
- Support OpenAI behind a narrow provider interface.
- Use one structured assessment call, deterministic scoring, and one
  structured CV-content call.
- Use a private DOCX template and small private YAML layout settings.
- Generate an editable DOCX targeted at one page. Do not render or refit it
  automatically; the user confirms pagination and exports the final PDF.
- Run one dispatcher with up to three spawned workers.
- Support initial automatic generation, mismatch override, bounded retry,
  checkpoint reuse, polling, and safe artefact-directory opening.

Deferred from Stage 1:

- LM Studio and additional model providers.
- Profile indexing, normalized profile versions, index review, and approval.
- Profile snapshots.
- AI-assisted fit analysis and rewriting.
- Reassessment, regeneration, and custom-instruction workflows.
- Legacy CV compatibility.
- Multi-launcher coordination and distributed queue guarantees.
- Final PDF tracking, in-app document editing, automated rendering, native
  notifications, and autonomous agents.

## Stage 2: Profile management and local providers

- Parse flexible Markdown into content-addressed source blocks and produce a
  normalized Profile Index with citations for every fact and rule.
- Add compact Profile Index review, explicit approval, staleness detection,
  source/schema/instruction versioning, and cache reuse.
- Keep existing assessments tied to their original profile/index hashes.
- Add LM Studio through the established structured-generation interface.
- Route capabilities independently and expose active provider/model choices.
- Add provider contract tests and opt-in evaluations for supported local
  models.

Stage 2 should not begin until Stage 1 shows that repeated raw-profile calls or
profile inconsistency create a real usability, cost, or auditability problem.

## Stage 3: Automatic one-page fitting

- Preserve stable content block identifiers through DOCX generation and the
  later final-PDF workflow.
- Produce a layout report that maps overflow back to CV content.
- Add a CvFitReviewer capability that may shorten or restructure the least
  relevant grounded content without inventing claims.
- Permit a small bounded number of revisions and retain every attempt.
- Expand synthetic evaluations to cover relevance preservation, unsupported
  claims, and deterministic stop conditions.

Stage 3 should retain visible failure after a bounded fitting limit and must
never silently discard grounded content or claim pagination it has not
verified.

## Stage 4: Workflow and operational maturity

- Add reassessment, CV regeneration, and generation-specific instructions when
  their lifecycle semantics are defined.
- Add multi-launcher dispatcher coordination only if JobHunter is run under
  more than one launcher.
- Expand model evaluations, queue diagnostics, retention controls, and
  operator tooling based on observed failures.
- Consider cross-platform rendering/opening only with an authoritative page
  renderer and explicit acceptance criteria.
- Consider final-document approval or PDF tracking only if manual Finder/editor
  handoff proves insufficient.

## Roadmap success criteria

- Every stage delivers a usable workflow and has a separate implementation
  plan with task-level tests.
- Private source data, secrets, prompts, and raw provider responses do not leak
  into tracked files, logs, browser errors, or public artefacts.
- Scores and lifecycle decisions remain deterministic after structured model
  output is validated.
- Generated claims remain traceable to private profile evidence.
- No stage begins by requiring deferred infrastructure from a later stage.
