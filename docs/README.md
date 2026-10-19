# Tutorials

Step-by-step walkthroughs for the staff-admin writes the `seolocal` plugin gives you through the `cade-mcp` connector. They work in Claude Desktop chat, Cowork and Claude Code alike: install `bwp-core` and `seolocal` (top-level README §1 or §4), sign in to `cade-mcp` with your `@blackwoodproductions.com` account, and you must be an SEO Local staff admin (`adminlevel 10`) or every write is refused.

| Tutorial | When you need it |
|---|---|
| [Change a domain's package with a new keyword set](package-change-with-keyword-set.md) | A customer moves package and hands you a CSV with their new keywords (the SEOM 5 → Agentic case). |
| [Replace or remove keywords on the current package](replace-keywords-on-the-same-package.md) | No package change, just a new set: drop some keywords, add others, keep the rest. |
| [Add Agentic keyword sets](add-agentic-keywords.md) | Add a main with its members to an Agentic domain without touching the existing ones. |
| [Troubleshooting package and keyword changes](package-change-troubleshooting.md) | A refusal you don't understand, a preview that isn't what you expected, or an old article that is still live. |

Every write is a dry run until you repeat it with `dry_run=false`, and every write repeats the domain in `confirm_domain`. The assistant does this for you; the tutorials show the calls so you can read what it did.

All domain names, ids and keywords in these pages are made-up examples. Look up the real ones with the SQL shown in each tutorial.
