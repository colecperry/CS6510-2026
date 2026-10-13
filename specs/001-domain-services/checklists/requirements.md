# Specification Quality Checklist: Service-Based Self-Checkout

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-10-13
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

Resolved during validation, across two iterations:

- **Technology leaked into the first draft.** Service names, port numbers, and a count of three services were all present. Removed: naming the services is a planning decision, and the spec now says only that the system is divided by business function. FR-008 requires the list to exist in the submission without fixing what it contains.
- **Success criteria were written in milliseconds.** SC-001 originally quoted a p95 latency target. Rewritten as "without a perceptible wait", with the measurement left to the plan. SC-003 similarly now refers to the previous implementation's error rate rather than naming a number.
- **"The gateway question" was initially an unstated assumption.** The load client can only target one address, which forces an entry point in front of the services, and that sits close to the rule forbidding service-to-service calls. Rather than leave it buried, it is now the first entry under Assumptions, with the reasoning the submission will need to make.
- **No [NEEDS CLARIFICATION] markers were used.** Three candidates were considered and resolved from existing repository context rather than asked: the number of services (left open, a planning decision), whether the previous weeks may be modified (no, the constitution forbids it), and whether containerisation is required (the brief says "whatever works", so local processes suffice). All three are recorded under Assumptions.

Resolved by `/speckit-clarify` on 2026-10-13, three questions:

- **The entry point is permitted**, as the caller-facing tier rather than a domain service. Was the largest open risk: read the other way, the single-address requirement would have been unsatisfiable. Now FR-005a, which also constrains it to hold no business rules and touch no data.
- **Three services**, one per business function: selling, stock, reporting. FR-003 now names them. This was deliberately left open by the spec, but it is the graded deliverable, so leaving it to the plan risked the main answer being decided by accident.
- **No latency threshold can fail the week.** Any slowdown is reported, not fixed. The design is revisited only on outright failure. Folded into SC-001 and the Assumptions entry, so the week cannot quietly turn into a performance-tuning exercise.

Still open, judged low impact:

- How far reporting may lag behind scanning, stated qualitatively rather than as a number. The previous implementations already recalculate only periodically, so some staleness is pre-existing and the acceptance test (SC-007) compares the ranking against those weeks rather than against a freshness target.
- What a caller sees when a service is stopped. The edge case says requests to the stopped service fail and others must not, which is enough to test against.
