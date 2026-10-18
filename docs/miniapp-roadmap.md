# Mini App and Gameplay Upgrade Plan

## Architecture
- Keep the Telegram bot as the game/event worker.
- Add a separately deployed HTTPS API and Telegram Mini App frontend.
- Validate Telegram Web App `initData` on every authenticated API request; never trust a user ID supplied by the browser.
- Use MongoDB as the source of truth for inventory, claims, store products, roles, settings, missions and events.

## Character eligibility
- Add `store_only: true` to characters that can only be purchased.
- Add `store_price` and `store_enabled` for shop configuration.
- Both group drops and `/hclaim` must exclude `store_only: true` characters.
- Admin/owner changes should be validated server-side and recorded in an audit log.

## Daily rewards
- `/daily` grants coins once per 24 hours.
- `/hclaim` grants one eligible character once per 24 hours, independently of the coin claim.
- Claims must use a conditional atomic database update so concurrent requests cannot claim twice.

## Role model
- Owner: all settings, admins, character catalogue, store, economy, events, maintenance and audit logs.
- Admin: only explicitly granted permissions, such as character editing, shop management, events or moderation.
- Admins cannot grant themselves permissions or promote another owner.

## Delivery phases
1. Security and data-integrity fixes; rotate any credentials ever committed to Git.
2. Atomic daily claims, shop-only character flags, purchase/listing consistency and regression tests.
3. Mini App read-only dashboard: profile, owned collection, searchable catalogue, balances and claim status.
4. Store checkout and owner-configurable character pricing/exclusivity.
5. Owner dashboard, permission-scoped admin panel, settings and audit logs.
6. Missions, achievements, events, group competitions and analytics.
7. Render staging deployment, smoke tests, then production rollout.

## Acceptance checks
- Two simultaneous daily claims yield exactly one reward.
- Store-only characters never appear in `/hclaim` or group drops.
- Two buyers cannot purchase the same listing.
- A purchase cannot drive a balance below zero.
- Non-admin users cannot call admin APIs, and admins cannot exceed their granted scopes.
- Mini App API rejects missing, expired or invalid Telegram initData.
- Inventory changes are idempotent or use database transactions/compensation with tests.
