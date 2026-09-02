<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

# Roamora engineering contract

Write production-quality application code: correct, understandable, secure,
typed, testable, maintainable, bounded, and easy to review.

Before changing behavior:

- read `rules.md` when it is available locally
- inspect the current implementation, tests, generated contracts, and relevant
  installed framework documentation
- identify the real-world process, data ownership, state transitions,
  invariants, trust boundaries, failure modes, and termination bounds
- define the permitted scope, protected behavior, acceptance criteria, tests,
  and stop conditions

Implementation rules:

- give each module, component, hook, route, service, schema, and function one
  clear responsibility; split by meaning, not arbitrary line counts
- organize domain behavior under feature boundaries and create shared
  abstractions only when concepts genuinely share the same rules
- keep important state ownership and data flow explicit; snapshot mutable UI
  intent when it defines an operation's meaning
- preserve strict TypeScript and Pydantic/Zod validation at trust boundaries;
  do not use `any`, unsafe assertions, ignore directives, or unchecked JSON
- fail with typed, sanitized terminal errors; never add silent model/provider
  fallback, fake production state, or unbounded retry/loop behavior
- keep secrets, internal URLs, raw exceptions, prompts, model output, and user
  content out of browser bundles and routine logs
- use the simplest correct design; do not add dependencies, infrastructure,
  state libraries, providers, persistence, or abstractions speculatively
- keep Next.js Client Component boundaries as narrow as practical and keep
  FastAPI routes focused on transport coordination
- prefer descriptive domain names, explicit state machines for meaningful
  workflows, and comments that explain non-obvious reasons rather than syntax
- test observable behavior and invariants at the cheapest reliable layer;
  preserve keyboard access, visible focus, semantic HTML, touch targets,
  responsive behavior, and safe status announcements
- do not refactor unrelated code; every phase and pull request must remain
  small enough to review and must document behavior, failures, security impact,
  model impact, tests, and known limitations

Before handoff or staging, review architecture, correctness, security,
reliability, maintainability, testing, frontend/backend boundaries, AI/model
invariants, and scope. A passing build alone is not sufficient.
