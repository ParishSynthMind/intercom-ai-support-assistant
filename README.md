# Intercom AI Support Assistant

[![tests](https://github.com/ParishSynthMind/intercom-ai-support-assistant/actions/workflows/tests.yml/badge.svg)](https://github.com/ParishSynthMind/intercom-ai-support-assistant/actions/workflows/tests.yml)

A custom AI layer for a human support team on **Intercom**. Every time a customer starts or replies to a conversation, Intercom sends a signed webhook; the assistant fetches the conversation through the Intercom REST API and has **Claude**:

- classify it: **intent** (billing, bug, how-to, account, cancellation...), **priority** and **sentiment**, plus a one-line summary,
- **tag** it in Intercom (`AI: billing`, `AI: high`, `AI: needs human`...) and, if you map intents to teams, **assign** it,
- look the answer up in your **help articles** (retrieval over a folder of Markdown, with **citations**),
- write a **draft reply as an internal note**, which only teammates can see.

Nothing reaches the customer until a person approves the draft (one CLI command or one API call). Then it is sent as a real reply, once.

**Where it sits next to Fin:** Fin is Intercom's own AI agent that answers customers directly. This project does not compete with it; it helps the *human* team with the conversations that reach them: triage, routing, a cited first draft and an approval step, using your own model, prompts and rules.

<!-- LIVE-STATUS -->
**Status (17 Nov 2026):** built and tested offline (196 tests in CI, Intercom faked with the API 2.18 shapes), and run end to end with the **real Claude model** (claude-haiku-5-5) against the local Intercom mock. The live run against a real Intercom workspace is waiting on one account step (see [What was proven live and what was mocked](#what-was-proven-live-and-what-was-mocked)).
<!-- /LIVE-STATUS -->

![Triage results from the recorded run](media/local-triage.png)

*Recorded run (`examples/local-run/run.json`): five customer messages through signed webhooks; Claude Haiku 5.5 triaged and drafted each one. Intercom is the local mock in this run.*

## How it works

```mermaid
flowchart LR
    I[Intercom] -->|webhook, X-Hub-Signature| W[/webhooks/intercom/]
    W -->|signature OK, new notification id| Q[background task]
    Q -->|GET /conversations/id| I
    Q --> G{guards}
    G -->|STOP / no AI| O[opt-out: tag + note, no draft]
    G --> K[help articles: BM25 top 3]
    K --> C[Claude: intent, priority, sentiment,<br/>summary, draft, citations]
    C --> V[code checks: enums, citations real,<br/>injection, length]
    V -->|POST tags, assign| I
    V -->|internal NOTE with draft| I
    P[person: approve] -->|reply as comment| I
```

1. **Webhook.** `POST /webhooks/intercom` checks `X-Hub-Signature` (HMAC-SHA1 of the raw body with the app's client secret, constant-time compare) before reading anything, drops a notification id it has already seen (Intercom retries), answers 200 at once (Intercom wants a reply within 5 seconds) and does the work after. `HEAD` answers Intercom's URL check. Topics: `conversation.user.created` and `conversation.user.replied`; anything else is ignored.
2. **Fetch.** `GET /conversations/{id}?display_as=plaintext` with the pinned `Intercom-Version: 2.18` header. The newest message written by the customer is the unit of work; its id is the idempotency key, so two webhooks, a manual run and the polling fallback can all point at it and only one draft is ever made.
3. **Guards.**
   - **Opt-out:** "STOP", "unsubscribe", "don't use AI", "no bots" put the contact on a suppression list. No AI call, no draft, ever again for that contact; a note tells the team to reply themselves.
   - **Prompt injection:** customer text goes to Claude fenced as data, with an instruction never to follow orders inside it. A code check looks for injection patterns too, so if the model misses one the draft is still flagged `AI: injection suspected` and `AI: needs human`.
   - **Size and spend caps:** customer text is capped per message; drafts are capped per day.
4. **Retrieve.** The newest customer message is matched against the help articles in `knowledge_base/` (each heading is a section; BM25 scoring; top 3). No vector database is needed for a help centre of tens to hundreds of articles, and every hit has a readable id like `refunds#2`.
5. **Claude.** One call per message with **structured outputs** (a strict JSON schema): intent, priority, sentiment, summary, language, draft, citations, needs_human, injection_suspected. The model id is one line in `config/assistant.yaml`.
6. **Check in code, not in the model.** Unknown enum values fall back to safe ones; any citation that was not one of the retrieved sections is removed; a draft with no valid citation is marked "needs a person"; long drafts are trimmed.
7. **Write back to Intercom.** Tags (created once, ids cached), an optional assignment (only when nobody is assigned yet, so a human's choice is never overridden), and an **internal note** with the summary, the flags, the suggested reply, its sources and the approve command. The note carries a short reference, so a run that crashed after the note landed does not post a second one.
8. **Approve.** `intercom-assistant approve <id>` (or `POST /drafts/<id>/approve` with the approver token) sends the draft as a real reply. A compare-and-set on the draft's status means two people clicking at once cannot send it twice. A send that fails is marked `failed` and alerts; it is never retried blindly (the reply may have landed).

![The internal note a teammate sees](media/local-note.png)

![A prompt-injection attempt: flagged, nothing approved, nothing revealed](media/local-injection-note.png)

## AI disclosure

Every customer-facing reply starts with a disclosure line (default: "I'm an AI assistant for {business}. A member of our team checked this reply before it was sent."). It is a config value, on by default; a blank value falls back to the default, and it can only be removed by recording the client's written request in `ai_disclosure_removed_by_written_request` (EU AI Act Article 50; our build rule B18).

## Setup

1. **Intercom app.** In your workspace: Settings > Integrations > Developer Hub > New app (an internal app for your own workspace). Its **Authentication** page shows the access token. The scopes needed: read and write conversations, read and write tags, read admins (plus read/write users and contacts only for the live test script).
2. **Secrets** (never in a file): environment variables or the macOS Keychain.

   | What | Environment variable | Keychain item |
   |---|---|---|
   | Intercom access token | `INTERCOM_ACCESS_TOKEN` | `intercom-access-token` |
   | App client secret (webhook signatures) | `INTERCOM_CLIENT_SECRET` | `intercom-client-secret` |
   | Anthropic API key | `ANTHROPIC_API_KEY` | `anthropic-api-key` |
   | Approver token for the HTTP approval API (optional) | `APPROVER_TOKEN` | `intercom-assistant-approver-token` |

3. **Install** (exact versions):
   ```bash
   python -m venv .venv && . .venv/bin/activate
   pip install -r requirements.lock && pip install -e . --no-deps
   ```
4. **Settings** in `config/assistant.yaml`: business name, region (`us`, `eu` or `au` picks `api.intercom.io`, `api.eu.intercom.io` or `api.au.intercom.io`), model, intent-to-team routing, caps, the kill switch.
5. **Run the webhook server** behind HTTPS (Intercom only calls HTTPS URLs):
   ```bash
   intercom-assistant serve --port 8000
   ```
   Then in the Developer Hub app: Webhooks > the endpoint URL `https://<your host>/webhooks/intercom`, topics `conversation.user.created` and `conversation.user.replied`.
6. **Work the queue:**
   ```bash
   intercom-assistant drafts            # pending drafts
   intercom-assistant approve 12        # send draft 12 to the customer
   intercom-assistant reject 12
   intercom-assistant process 123456    # handle one conversation now, no webhook needed
   intercom-assistant poll --minutes 60 # catch anything a missed webhook skipped
   ```

## Try it without any account

```bash
pytest -v                                # 196 tests, Intercom and Claude faked
python scripts/local_run.py --fake-ai    # the whole flow through the real webhook server, no keys
python scripts/local_run.py              # same, with the real Claude model (needs the Anthropic key)
```

## What was proven live and what was mocked

| Part | Status |
|---|---|
| Claude triage + draft + citations (claude-haiku-5-5, structured outputs) | **Proven live**: real API calls in `examples/local-run/` |
| Webhook signature check, duplicate delivery, background processing, approval API | Tested through the real FastAPI app with signed requests; Intercom side mocked |
| Intercom REST calls (conversations, tags, notes, replies, assignment, contacts) | <!-- LIVE-ROW -->Mocked (a fake built from Intercom's 2.18 OpenAPI spec). Live run on a real workspace: pending one account step (accepting Intercom's developer terms)<!-- /LIVE-ROW --> |
| Real Intercom webhook delivery to a public HTTPS URL | Not run live |
| Assignment to a real team | Mocked only (the trial workspace has no teams set up) |
| Heartbeat / failure alerts | Tested offline; alert targets are the client's (env vars) |

## Honest limits

- The help-article retrieval is word-based (BM25). It is explainable and needs no extra service, but it does not match across languages: an English help centre will not match a French question, so those drafts come out "needs a person" (seen in the recorded run). For multilingual help centres, add translated articles or swap in an embedding search.
- The disclosure line is in English by default; set `ai_disclosure` per workspace.
- Only text is handled: attachments, images and voice notes are ignored.
- The assistant never closes, snoozes or deletes anything in Intercom, and never replies on its own.
- Tickets (Intercom's separate ticket objects) are out of scope; conversations only.
- Never used for a client yet: this is a portfolio build.

## Security

- **Signatures first:** nothing is parsed or stored before the HMAC check passes; unsigned or malformed signatures get 401.
- **Customer text is data:** fenced in the prompt, never followed as instructions; a code check backs the model up.
- **A person approves anything customer-facing.** The AI can only write internal notes and tags.
- **No personal data in logs:** each step logs one JSON line, with emails, phone numbers and card-like numbers masked.
- **Secrets** come from the environment or the Keychain, never files; `.env` and the database are git-ignored.
- **Kill switch:** `paused: true` (or `ASSISTANT_PAUSED=true`) stops every AI call and every write to Intercom, including approvals.
- **Least access:** the Intercom token needs conversation, tag and admin scopes only.
- **Dependencies pinned** (`requirements.lock`), `pip-audit` in CI: 0 known vulnerabilities on 17 Nov 2026 (before 0, after 0). Actions pinned to commit SHAs.

## Versions (pinned on purpose)

| Platform | Version pinned | Where | Vendor end date | Review |
|---|---|---|---|---|
| Intercom REST API | `2.18` (Intercom-Version header) | `config.INTERCOM_API_VERSION` | none published (Intercom lists no end dates for numbered versions) | 2027-02 |
| Anthropic API | SDK `anthropic==2.9.0` (sends anthropic-version 2026-10-21) | `requirements.txt` | none published | 2027-02 |
| Claude model | `claude-haiku-5-5` | `config/assistant.yaml` | none announced | 2027-02 |
| Python | 3.12 (CI) | `.github/workflows/tests.yml` | 2028-10 | 2027-10 |

## When something breaks (Tier 1 runbook)

| Symptom | Likely cause | Fix |
|---|---|---|
| Every webhook gets 401 | Client secret rotated or wrong app | Copy the client secret from the app's Basic Info page into `intercom-client-secret`; send a test notification from the Developer Hub |
| Calls fail with 401 `unauthorized` | Access token revoked or app uninstalled | Reinstall the app / copy the new token into the Keychain; run `intercom-assistant process <id>` on one conversation |
| `intercom_version_invalid` | Version string changed by hand | Keep `2.18` (or upgrade on purpose after reading the changelog and re-running the tests) |
| 429 `rate_limit_exceeded` repeatedly | Burst of conversations | The client waits for `Retry-After`/`X-RateLimit-Reset`; lower volume or ask Intercom for a higher limit |
| No drafts for hours, nothing in logs | Webhook subscription disabled (Intercom disables it after repeated failures or a 410) | Re-enable in the Developer Hub; run `intercom-assistant poll --minutes 1440` to catch up; `check-heartbeat` alerts on this |
| Drafts say "model declined" | Claude refusal or retired model id | Check the model id in config against Anthropic's models page; a person replies meanwhile |
| A send shows `failed` | Network or Intercom error during the reply | Open the conversation: if the reply is not there, `intercom-assistant retry <id>` then `approve <id>` |

## Tests

196 offline tests: signatures, settings and secrets, the guards, retrieval, the API client against the fake (headers, retries on 429 and 5xx, no retry of writes on 5xx, timeouts), the AI output checks, the whole service (idempotency, opt-out, injection, routing, caps, alerts, crash recovery), the webhook app and approval API, the store and the CLI.

```bash
ruff check . && pytest -v
```

## Licence

MIT. Built by ParishSynthMind with Claude Code.
