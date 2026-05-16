# Secrets & Credentials — MANDATORY

**NEVER hardcode API keys, passwords, tokens, or connection strings.** This is enforced at every level.

## Rules

1. **All credentials live in `.env.*` files** — `.env.db`, `.env.polygon`, `.env.alpaca`, `.env.n8n` (gitignored)
2. **Python scripts use `load_env()` pattern** — see any `scripts/compute_*.py` for the template
3. **Shell helpers source `.env.*` automatically** — use `scripts/n8n_api.sh` for n8n REST API calls
4. **Docker compose uses `env_file:` directives** — never put secrets in `environment:` blocks
5. **`.env.*.example` files** are committed with placeholders; real keys are gitignored
6. **If a key is invalid/rotated** — regenerate in the service UI, update `.env.*`, restart affected services

## ❌ WRONG — NEVER Do This

```bash
# Hardcoding keys in curl commands
curl -H "X-N8N-API-KEY: eyJhbGciOi..." http://localhost:5678/api/v1/workflows

# Hardcoding DB passwords in psql commands
psql -U clawstreet -d clawstreet -h localhost -p 5432 -w

# Pasting tokens into Python scripts
conn = psycopg2.connect(password="hardcoded_password")
```

## ✅ RIGHT — Always Do This

```bash
# Use the helper script (sources .env automatically)
./scripts/n8n_api.sh list
./scripts/n8n_api.sh activate <workflow_id>

# In Python, use load_env()
load_env(".env.db")
conn = psycopg2.connect(
    host=os.environ["POSTGRES_HOST"],
    port=int(os.environ["POSTGRES_PORT"]),
    ...
)

# In shell, source the env file
set -a; source .env.n8n; set +a
curl -H "X-N8N-API-KEY: $N8N_API_KEY" ...
```

## Key Rotation

If an API key is invalidated (common after n8n container restarts):
1. Open the service UI (e.g., n8n → Settings → API → Create API Key)
2. Update the corresponding `.env.*` file with the new key
3. Restart affected services: `docker compose up -d <service>`
4. Verify with the helper script: `./scripts/n8n_api.sh list`

## .env File Locations

| File | Contents | Used By |
|------|----------|---------|
| `.env.db` | POSTGRES_HOST, PORT, DB, USER, PASSWORD | worker, scripts |
| `.env.polygon` | POLYGON_API_KEY, Flat Files S3 creds | Polygon ingestion scripts |
| `.env.alpaca` | ALPACA_API_KEY, SECRET, BASE_URL | Alpaca trading scripts |
| `.env.n8n` | N8N_API_KEY, auth, encryption key, port | n8n_api.sh |

All are gitignored. `.env.*.example` files are committed with placeholder values.

## Pitfalls

- **Truncated keys**: JWT tokens are 200+ chars. If `.env.n8n` has `N8N_API_KEY=eyJhbG...nB2c`, the key was truncated and is unusable. Regenerate it.
- **`os.environ.setdefault()`**: Won't override existing env vars. Container env vars take precedence over `.env.*` values — this is correct behavior.
- **Never echo/print secrets in logs**: Use `echo "Key length: ${#N8N_API_KEY}"` instead of `echo $N8N_API_KEY`.