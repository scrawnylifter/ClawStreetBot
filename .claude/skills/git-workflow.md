# git-workflow

**MANDATORY — follow this workflow for ALL code changes. No exceptions.**

All code changes follow a structured Git workflow. No direct commits to `main`.

## MANDATORY: Before Starting Work

```bash
git checkout main
git pull origin main
git checkout -b feature/<phase>-<description>   # or fix/, refactor/, docs/
```

**NEVER commit directly to `main`.** Always create a feature branch first.

## MANDATORY: Commit Messages

```
<type>(<scope>): <short summary>
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`
Scope: module or table name (e.g., `ohlcv`, `gex_dex`, `watchlist`, `docker`)

Examples:
- `feat(ohlcv): add Polygon.io OHLCV ingestion script`
- `fix(options): handle missing open_interest from Alpaca paper tier`
- `docs(obsidian): update Project Roadmap with Phase 2 progress`

## MANDATORY: Before Finishing — Push & Sync

```bash
git add -A
git commit -m "feat(<scope>): <description>"
git push -u origin feature/<name>
```

Then **run `/docs-sync`** to update README.md and Home.md.

Then **wait for Hermes to review and merge.** Do NOT merge to main yourself.

After Hermes merges: `git checkout main && git pull origin main`

## MANDATORY: Single Writer Convention

| Asset | Who writes | You can write? |
|-------|-----------|--------------|
| `scripts/*.py` | You on feature branch | ✅ |
| `db/init/*.sql` | You on feature branch | ✅ |
| `config/*.yml` | You on feature branch | ✅ |
| `docker-compose.yml` | You on feature branch | ✅ |
| `CLAUDE.md` | **Hermes ONLY** | ❌ NEVER edit |
| `obsidian/vault/*` | **Hermes ONLY** | ❌ NEVER edit |
| `README.md` | Both (after merge) | ✅ |
| `.env.*` | **Hermes ONLY** | ❌ NEVER touch |

**Rule: never edit the same file as Hermes at the same time.**

## Branch Naming

```
feature/<phase>-<description>   # New features (e.g., feature/phase2-iv-outlier-cleaning)
fix/<short-description>         # Bug fixes
refactor/<short-description>    # Code cleanup
docs/<short-description>        # Documentation only
```

## Full Lifecycle

1. `git checkout main && git pull origin main`
2. `git checkout -b feature/<name> main`
3. Build the feature, commit incrementally
4. `git push -u origin feature/<name>`
5. Run `/docs-sync`
6. Wait for Hermes to review, open PR, and squash-merge
7. `git checkout main && git pull origin main`
8. `git branch -d feature/<name>`