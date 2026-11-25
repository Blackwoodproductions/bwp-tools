---
name: related-domains
description: List the CADE domains whose business is related to a given domain (link partners, peers in the same trade) through the cade-mcp `related_domains` tool. Use for "which domains are related to {domain}", "related domains for {domain}", "link partners for {domain}", "who else is in {domain}'s line of business". Reads only; writes nothing and places no link.
---

# cade: related-domains

1. Call `cade-mcp` `related_domains { domain }`. A bare domain, a URL or the bwp alias all work. If it fails with "Pomerium identity", tell the user to reconnect the cade-mcp connector (Settings → Connectors) and stop.
2. Show one table, highest score first: `domain | level | score | shared categories`. Under it, write "N related out of M candidates checked" (M is `candidates_checked`).

## How it decides

Candidates are the other CADE domains with a category that shares its first two levels with one of this domain's categories, for example `/law & government/legal`. Jev reads this domain's description and services against each candidate's primary keyword and services, and scores each candidate from 0 to 3:

| level | score | meaning |
|---|---|---|
| `most_related` | 2.2 and up | mostly the same line of business |
| `related` | 1.7 to 2.2 | closely related services that the same customer would also look for |

Anything below 1.7 is left out.

## What to tell the user

- **It returns only domains it is sure about.** In the proof, every domain it returned was related, but only about half of the related domains came back. Most of the misses are complementary trades (painter ↔ flooring, lawyer ↔ chiropractor). A short or empty list does not mean there are no partners.
- **`candidates_checked: 0`** has one of three causes: this domain has no business description, its stored description is a bot-challenge page, or no other domain shares a category with it. The domain's context needs a crawl or a fix first.
- **Nothing is written.** This only lists domains; it doesn't reserve or place a link.

## Errors

- **404:** CADE doesn't know the domain. Check the spelling, or look it up with `execute_sql_cade`: `SELECT domain, bwp_domain FROM domains WHERE domain ILIKE '%name%' LIMIT 10`.
- **503 `RELATED_DOMAINS_UNAVAILABLE`:** a candidate needed scoring and Jev couldn't score it. Retry after 30 seconds. On this error the tool never returns candidates that weren't checked.

## Cost

Scores are stored. The first lookup for a domain asks Jev about every candidate: about $0.003 for a typical domain. A repeat lookup is free unless a business's facts changed since. Before looping over many domains, ask the user first.
