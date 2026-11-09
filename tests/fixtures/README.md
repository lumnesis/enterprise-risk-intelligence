# Fixture provenance

`incidents.json` is a synthetic, sanitized Statuspage-shaped contract fixture with
placeholder identifiers, example.test links and generic incident descriptions.
It contains no private data or credentials and is **not an observed Coinbase incident**.
It exercises the expected incidents envelope, nested updates and components,
offset timestamps and optional fields. It must not be used for business conclusions.

Live Coinbase API and documentation requests were blocked by the cloud proxy
(HTTP 403) during implementation. Consequently this fixture does not establish
compatibility with an observed live response. After access is enabled, verify the
response keys and types, sanitize a small sample, and add it as a separate fixture
with its retrieval date. Keep all automated tests independent of live requests.
