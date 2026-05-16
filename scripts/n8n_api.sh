#!/usr/bin/env bash
# n8n REST API helper — sources credentials from .env.n8n
# Usage: ./scripts/n8n_api.sh [list|activate|deactivate|delete|import] [workflow_id_or_file]
#
# Examples:
#   ./scripts/n8n_api.sh list
#   ./scripts/n8n_api.sh activate CJNUeFiG4Sp1Hsyj
#   ./scripts/n8n_api.sh deactivate CJNUeFiG4Sp1Hsyj
#   ./scripts/n8n_api.sh delete CJNUeFiG4Sp1Hsyj
#   ./scripts/n8n_api.sh import n8n/workflows/derived_daily.json
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env.n8n"

# Source the env file
if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: $ENV_FILE not found" >&2
    exit 1
fi

# Parse N8N_API_KEY from .env.n8n (handles full JWT tokens)
N8N_API_KEY=$(grep '^N8N_API_KEY=' "$ENV_FILE" | cut -d'=' -f2-)
N8N_HOST_PORT=$(grep '^N8N_HOST_PORT=' "$ENV_FILE" | cut -d'=' -f2-)
N8N_HOST_PORT="${N8N_HOST_PORT:-5678}"
BASE_URL="http://localhost:${N8N_HOST_PORT}/api/v1"

if [[ -z "$N8N_API_KEY" || "$N8N_API_KEY" == "replace-with-api-key-from-n8n-ui" ]]; then
    echo "ERROR: N8N_API_KEY not set or is placeholder in $ENV_FILE" >&2
    echo "Generate one at http://localhost:${N8N_HOST_PORT} → Settings → API → Create API Key" >&2
    exit 1
fi

CURL_ARGS=(-s -H "X-N8N-API-KEY: $N8N_API_KEY" -H "Content-Type: application/json")

case "${1:-list}" in
    list)
        curl "${CURL_ARGS[@]}" "$BASE_URL/workflows" | python3 -c "
import json, sys
data = json.load(sys.stdin)
workflows = data.get('data', data) if isinstance(data, dict) else data
for w in sorted(workflows, key=lambda x: x['name']):
    status = '✅' if w['active'] else '❌'
    print(f\"{status} {w['name']:25s} id={w['id']} nodes={len(w['nodes'])}\")
"
        ;;
    get)
        [[ -z "${2:-}" ]] && { echo "Usage: $0 get <workflow_id>"; exit 1; }
        curl "${CURL_ARGS[@]}" "$BASE_URL/workflows/$2" | python3 -c "
import json, sys
w = json.load(sys.stdin)
print(f\"Name: {w['name']}\")
print(f\"Active: {w['active']}\")
print(f\"Nodes: {[n['name'] for n in w['nodes']]}\")
"
        ;;
    activate)
        [[ -z "${2:-}" ]] && { echo "Usage: $0 activate <workflow_id>"; exit 1; }
        curl -X POST "${CURL_ARGS[@]}" "$BASE_URL/workflows/$2/activate"
        echo ""
        ;;
    deactivate)
        [[ -z "${2:-}" ]] && { echo "Usage: $0 deactivate <workflow_id>"; exit 1; }
        curl -X POST "${CURL_ARGS[@]}" "$BASE_URL/workflows/$2/deactivate"
        echo ""
        ;;
    delete)
        [[ -z "${2:-}" ]] && { echo "Usage: $0 delete <workflow_id>"; exit 1; }
        curl -X DELETE "${CURL_ARGS[@]}" "$BASE_URL/workflows/$2"
        echo ""
        ;;
    import)
        [[ -z "${2:-}" ]] && { echo "Usage: $0 import <file.json>"; exit 1; }
        PAYLOAD=$(python3 -c "
import json
with open('$2') as f:
    wf = json.load(f)
# Remove fields that conflict with creation
for k in ['id','createdAt','updatedAt','activeVersionId','versionId','triggerCount','shared','activeVersion','pinData','staticData','meta']:
    wf.pop(k, None)
wf['active'] = True
print(json.dumps(wf))
")
        curl -X POST "${CURL_ARGS[@]}" -d "$PAYLOAD" "$BASE_URL/workflows"
        echo ""
        ;;
    *)
        echo "Usage: $0 [list|get|activate|deactivate|delete|import] [id_or_file]"
        echo ""
        echo "Commands:"
        echo "  list              List all workflows and their status"
        echo "  get <id>          Show workflow details"
        echo "  activate <id>     Activate a workflow"
        echo "  deactivate <id>   Deactivate a workflow"
        echo "  delete <id>       Delete a workflow"
        echo "  import <file>     Import a workflow JSON and activate it"
        ;;
esac