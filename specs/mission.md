# Mission

## What we're building

A clinic booking service for **Task 1 of the Koya Cohort 4 assessment** (AI Automation Developer):

1. **An HTTP API**: patients list appointment slots, book one, look up a booking, cancel it, and page through their own bookings.
2. **A simple web page** built on that API: see the available slots, book one, and see the confirmation.

Both are deployed at a public `https` URL and stay up, **unchanged**, after submission until Koya gets in touch.

## Source of truth

- The contract is the OpenAPI spec at <https://koyaacademy.com/assessment-api/openapi.json>, and Koya's automated checks call the API exactly as it describes. **Follow it closely.** Where this repo's specs and the OpenAPI spec disagree, the OpenAPI spec wins.
- The brief is at <https://tally.so/r/1WPoL4>.

## Goal: all five levels, each one done properly before the next

| Level | Outcome |
|---|---|
| 1 | List slots (`GET /slots`, optional `provider` filter, earliest first), book (`POST /bookings` → 201 `confirmed`), look up (`GET /bookings/{id}`), and replace all data (`POST /reset`). |
| 2 | Every request needs `X-API-Key` (401 otherwise). A booking with a missing or empty field or an invalid email gets 400, an unknown slot gets 404, and a slot that has already started gets 400. Names and emails are trimmed, and emails are stored in lower case. |
| 3 | No slot is ever double-booked, **even under simultaneous requests** (409). `DELETE /bookings/{id}` cancels a booking and frees the slot (409 if it's already cancelled). |
| 4 | `Idempotency-Key`: the same key with the same body replays the original response (same status and same booking `id`), and the same key with a different body gets 422. `GET /bookings?email=` uses cursor pagination, oldest first, with `limit` from 1 to 50 (400 otherwise). |
| 5 | At most 5 booking attempts per email per minute, failed attempts included. The 6th gets 429 with a `Retry-After` header in whole seconds. Other emails aren't affected. |

## Non-negotiables

- **Every error body is `{"error": "<message>"}`**. That includes framework validation errors, malformed JSON, and unknown routes.
- **Status codes exactly as the spec says.** In particular, a body validation failure is **400, not FastAPI's default 422**. The spec reserves 422 for an idempotency key reused with a different body.
- **Bookings must survive the next request.** They're stored in Postgres, never in process memory.
- **Correct under concurrency.** Double-booking and idempotency guarantees are enforced by the database, not by checks in application code that a race can slip past.
- **Always shippable.** Every phase ends deployed and working, so if the clock runs out, whatever is live is a complete submission up to that level.

## Deadline and submission

- **Due by midnight, Sunday 15 November 2026**, submitted from the email you applied with. Grading is rolling, so submitting earlier is better.
- The form asks for the API base URL, the API key, the web page URL, the GitHub repo (public, or invite `quadri40`), and a Loom of 5 minutes or less. The Loom shows the page working, walks through the code, and covers how far we got, the two biggest decisions, and what we'd do next.

## Out of scope

User accounts or patient login, creating slots from the UI (slots arrive only through `/reset`), email or SMS notifications, a provider or admin dashboard, multiple clinics, time zones beyond UTC, and anything not in the OpenAPI spec.
