# docs-sync

**MANDATORY workflow — run this before closing any PR.**

After any change to project structure (new scripts, new DB tables, new docs, completed phases), update these three documentation files to match reality.

## When to Sync

- After creating new scripts in `scripts/`
- After adding new DB migration files in `db/init/`
- After creating new Obsidian docs
- After completing or starting a phase item
- After adding new dependencies or API integrations
- After any `.gitignore` changes

## Files to Sync

| File | Role | What to update |
|------|------|---------------|
| `README.md` | Public-facing onboarding | Project tree, Quick Start, TODO/phase status |
| `obsidian/vault/Home.md` | Obsidian dashboard | Navigation table, Quick Links, Current Status checkboxes |
| `CLAUDE.md` | AI agent context | File tree, phase status, data sources, table descriptions |

**⚠️ CRITICAL: You MUST NOT edit CLAUDE.md — that file is owned by Hermes. Update README.md and Home.md only. Hermes will sync CLAUDE.md separately.**

## Sync Checklist

- [ ] `README.md` project tree lists ALL files in `scripts/`, `db/init/`, and `obsidian/vault/` subfolders
- [ ] `README.md` Quick Start includes all setup steps (env files, pip install, ingestion commands)
- [ ] `README.md` TODO checkmarks match actual progress (`[x]` done, `[ ]` pending)
- [ ] `obsidian/vault/Home.md` navigation table lists all folders and new docs
- [ ] `obsidian/vault/Home.md` wikilinks point to real files (`[[Greeks Strategy]]` → `Greeks Strategy.md`)
- [ ] `obsidian/vault/Home.md` `updated` date in frontmatter is current
- [ ] Separate commit for doc changes: `docs: sync README, Home for <change>`

## Rules

- **NEVER edit CLAUDE.md risk rules or trading parameters** — Obsidian vault is the source of truth for those. Only sync structural/factual info.
- **NEVER edit CLAUDE.md at all** — Hermes owns that file. You sync README.md and Home.md only.
- **Obsidian wikilinks use filenames**, not H1 titles. `[[Greeks Strategy]]` must match `Greeks Strategy.md` exactly.
- **README uses pipe tables** (GitHub renders them). Home.md uses pipe tables too. No `||` double-pipe tables.
- **Home.md frontmatter** — always update the `updated` date.
- **Separate docs commit from code commit** — never mix doc updates with feature code in one commit.