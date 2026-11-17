# What this build needs from an account (and what only Parish can do)

| Step | Who | Status (2026-11-17) |
|---|---|---|
| Intercom workspace (free 14-day trial, no card) | Parish | Done: workspace exists and is signed in in the automation Chrome |
| Accept Intercom's Developer Guidelines, Terms of Service and Privacy Policy in the Developer Hub (one "Accept and Continue" button) | **Parish only** (accepting terms is his) | **Waiting**: emailed and Telegrammed 13:55 |
| Create a private Developer Hub app ("AI Support Assistant (test)") and read its access token into the Keychain item `intercom-access-token` | Neo | After the step above |
| Client secret for webhook signatures into Keychain item `intercom-client-secret` | Neo | After the step above (only needed for the live webhook test) |
| Anthropic key | Keychain item `anthropic-api-key` (already there) | Done |

No card, phone verification or payment is needed for any of this. The trial ends 14 days after sign-up; nothing is billed unless someone subscribes.
