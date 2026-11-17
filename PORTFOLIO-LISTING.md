# Portfolio listing: Intercom AI Support Assistant

Status: DRAFT, waiting for the live test on the Intercom trial workspace. Posting was pre-approved by Parish on 17 Nov 2026 for after the live test, with the listing honest about live versus mocked. Wording below is the post-live version; if the live run cannot happen, use the "if not live" lines.

## Upwork (portfolio project)
**Title:** Intercom AI support assistant: Claude triage, cited draft replies, human approval

**Role:** AI integration developer

**Description (under 600 characters):**
An AI layer for a support team on Intercom. Signed webhooks bring each new conversation in; Claude classifies intent, priority and sentiment, tags and routes it, finds the answer in the help articles and writes a draft reply with its sources as an internal note. Nothing reaches the customer until a person approves it, and it is sent once. Opt-out list, prompt-injection guard, AI disclosure, kill switch, no personal data in logs. Tested live on a real Intercom workspace with real Claude; 196 automated tests.

*If not live:* replace the last sentence with "Built on Intercom's API 2.18 with a full mock and run with real Claude; 196 automated tests."

**Skills (5, closest honest Upwork tags; probe at posting time):** Intercom, Customer Support, API Integration, Python, Claude API (fallbacks if a tag is missing: Chatbot Development, FastAPI, AI Agent Development)

**Images:** `media/thumbnail-4x3.png` (cover, 1200x900), `media/live-*.png` (real Intercom inbox, after the live run) or `media/local-triage.png`, `media/local-note.png`, `media/local-injection-note.png`.
**Web link block:** https://github.com/ParishSynthMind/intercom-ai-support-assistant

## PeoplePerHour (200 characters max, no links)
Intercom AI assistant: Claude tags and routes each chat, drafts a cited reply as an internal note, and a person approves before it is sent. Tested live with 196 tests. Code available on request.

(*If not live:* "Intercom AI assistant: Claude tags and routes each chat, drafts a cited reply as an internal note, and a person approves before it is sent. 196 tests. Code available on request.")

## LinkedIn
**Featured card title:** Intercom AI Support Assistant
**Blurb:** Claude triages every Intercom conversation, tags and routes it, and drafts a cited reply as an internal note; a person approves before anything is sent.
**Link:** https://github.com/ParishSynthMind/intercom-ai-support-assistant
**Skills to add:** Intercom (and Customer Support Automation if offered). Nothing posted to the feed.

## GitHub
Description and topics set (intercom, claude, anthropic, customer-support, ai-agent, rag, webhooks, fastapi, python); CI badge in the README.

## Honesty checks before posting
- No "clients", "customers served" or "production" claims (grep the text).
- Live claims only for what `examples/live-run/` shows.
- Fin is Intercom's own AI agent: we never say we replace it.
