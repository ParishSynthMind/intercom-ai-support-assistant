# What this build needs from an account (and what only Parish can do)

| Step | Who | Status (2026-11-17) |
|---|---|---|
| Intercom workspace (free 14-day trial, no card) | Parish | Done: workspace exists and is signed in in the automation Chrome |
| Accept Intercom's Developer Guidelines, Terms of Service and Privacy Policy in the Developer Hub (one "Accept and Continue" button) | **Parish only** (accepting terms is his) | Done by Parish (seen 15:44) |
| Create a private Developer Hub app ("AI Support Assistant (test)") and read its access token into the Keychain item `intercom-access-token` | Neo | Done 15:45 (app package 221408) |
| Client secret for webhook signatures into Keychain item `intercom-client-secret` | Neo | Done 15:47 |
| Anthropic key | Keychain item `anthropic-api-key` (already there) | Done |

No card, phone verification or payment is needed for any of this. The trial ends 14 days after sign-up; nothing is billed unless someone subscribes.

Left in the test workspace: the app's webhook still points at the expired test tunnel URL (deliveries will fail harmlessly; Intercom may disable the subscription), and the test contacts/conversations made by the live scripts (example.com addresses).
