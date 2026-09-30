---
name: link-sync
description: Run a BRON link sync for a migrated site and explain the result. Use for "link sync <domain>", "resync links on <domain>", "links missing on <domain>", or when watch flags a stale link sync. Writes to the live site.
---

# wp-migration: link-sync

**Writes to the live customer site.** One domain per confirmation.

1. `cutover_record(domain)`: must be at `finish`; otherwise explain (CADE answers 202 without queueing for a site that is not cut over) and stop.
2. Note the current `link_synced_at`. Confirm with the user, then `link_sync(domain, confirm_domain=domain)`. If the tool says another operator owns the site, tell the user who; pass `force=true` only if they say a hand-over was agreed.
3. Poll `cutover_record` every ~30 s (up to ~10 min, telling the user you are waiting) until `link_synced_at` moves or `last_error` changes.
4. Report: synced at ...; or the error explained via `wp-migration:site` → `references/codes.md`. Afterwards offer `wp-migration:verification` (rows 10 and 11 read link-sync results).

Refusals:
- `CADE 409: BRON link sync is disabled (BRON_LINK_SYNC_ENABLED)`: the estate-wide link sync is switched off. Tell the user to ask the migration lead; do not retry. CADE checks this before anything else, so it applies to any domain.
- A 202 whose answer says "not cut over; nothing queued": nothing ran. Go back to step 1.
