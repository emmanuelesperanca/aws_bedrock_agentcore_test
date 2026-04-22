#!/usr/bin/env bash
# infrastructure/scripts/deploy.sh
#
# Full Neoson deployment orchestrator.
# Deploys CloudFormation stacks and registers all BedrockAgentCore runtimes.
#
# USAGE:
#   ./deploy.sh [ENVIRONMENT] [AWS_ACCOUNT_ID] [ENTRA_TENANT_ID] [ENTRA_CLIENT_ID]
#
# EXAMPLE:
#   ./deploy.sh dev 123456789012 aaaaaaaa-... bbbbbbbb-...
#
# FLOW:
#   1. Deploy CFn Stack 00: IAM + API Gateway + Athena Workgroup
#   2. Upload OpenAPI schemas to S3
#   3. Deploy CFn Stack 01: Lambda functions + Action Groups scaffold
#   4. Build & push container images to ECR
#   5. Deploy each AgentCore runtime (supervisor + 5 sub-agents)
#   6. Populate sub-agent ARNs back into supervisor's env vars
#
set -euo pipefail

ENVIRONMENT="${1:-${ENVIRONMENT:-dev}}"
AWS_ACCOUNT_ID="${2:-${AWS_ACCOUNT_ID:?'Set AWS_ACCOUNT_ID'}}"
ENTRA_TENANT_ID="${3:-${ENTRA_TENANT_ID:?'Set ENTRA_TENANT_ID'}}"
ENTRA_CLIENT_ID="${4:-${ENTRA_CLIENT_ID:?'Set ENTRA_CLIENT_ID'}}"
AWS_REGION="${AWS_REGION:-us-east-1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CFN_DIR="${REPO_ROOT}/infrastructure/cloudformation"
SCHEMAS_DIR="${REPO_ROOT}/lambdas/schemas"

STACK_PREFIX="neoson-${ENVIRONMENT}"
SCHEMA_BUCKET="${STACK_PREFIX}-schemas-${AWS_ACCOUNT_ID}"

echo "═══════════════════════════════════════════════════"
echo "  Neoson Full Deploy"
echo "  Environment : ${ENVIRONMENT}"
echo "  Account     : ${AWS_ACCOUNT_ID}"
echo "  Region      : ${AWS_REGION}"
echo "═══════════════════════════════════════════════════"

# ── Helper to wait for CFn stack ──────────────────────────────────────────────
cfn_deploy() {
  local STACK_NAME="$1"
  local TEMPLATE="$2"
  shift 2
  echo ""
  echo "▶ Deploying CloudFormation stack: ${STACK_NAME}"
  aws cloudformation deploy \
    --stack-name "${STACK_NAME}" \
    --template-file "${TEMPLATE}" \
    --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
    --region "${AWS_REGION}" \
    "$@"
  echo "✓ ${STACK_NAME} deployed"
}

# ── Step 1: Stack 00 — IAM + API Gateway ─────────────────────────────────────
cfn_deploy "${STACK_PREFIX}-foundation" \
  "${CFN_DIR}/00-iam-and-gateway.yaml" \
  --parameter-overrides \
    "Environment=${ENVIRONMENT}" \
    "EntraIdTenantId=${ENTRA_TENANT_ID}" \
    "EntraIdClientId=${ENTRA_CLIENT_ID}"

# ── Step 2: Create schema bucket and upload OpenAPI specs ─────────────────────
echo ""
echo "▶ Uploading OpenAPI schemas to s3://${SCHEMA_BUCKET}/schemas/"
aws s3 mb "s3://${SCHEMA_BUCKET}" --region "${AWS_REGION}" 2>/dev/null || true
aws s3 sync "${SCHEMAS_DIR}" "s3://${SCHEMA_BUCKET}/schemas/" \
  --region "${AWS_REGION}" \
  --exclude "*" --include "*.yaml"
echo "✓ Schemas uploaded"

# ── Step 3: Stack 01 — Lambda + Action Groups ─────────────────────────────────
cfn_deploy "${STACK_PREFIX}-action-groups" \
  "${CFN_DIR}/01-bedrock-agent-action-groups.yaml" \
  --parameter-overrides \
    "Environment=${ENVIRONMENT}" \
    "Stack00ExportsPrefix=neoson-foundation" \
    "SchemaBucketName=${SCHEMA_BUCKET}"

# ── Step 4: Build & Push container images ─────────────────────────────────────
echo ""
echo "▶ Building and pushing container images..."
"${SCRIPT_DIR}/build_push_ecr.sh" "${ENVIRONMENT}" "${AWS_ACCOUNT_ID}" "${AWS_REGION}"

ECR_BASE="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

# ── Step 5: Deploy AgentCore runtimes ─────────────────────────────────────────
deploy_agentcore() {
  local AGENT_DIR="$1"
  local AGENT_NAME="$2"
  echo ""
  echo "▶ Deploying AgentCore: ${AGENT_NAME}"
  pushd "${AGENT_DIR}" > /dev/null
  # Update image URI in .agentcore.yaml before deploy
  # (agentcore CLI reads the YAML for image: ecr_repository field)
  AGENT_ARN=$(agentcore deploy --env "${ENVIRONMENT}" --output-format json 2>&1 \
    | grep '"agentArn"' | sed 's/.*"agentArn": *"\([^"]*\)".*/\1/')
  echo "✓ ${AGENT_NAME} ARN: ${AGENT_ARN}"
  popd > /dev/null
  echo "${AGENT_ARN}"
}

# Deploy sub-agents first so their ARNs are known for the supervisor
GOVERNANCE_ARN=$(deploy_agentcore \
  "${REPO_ROOT}/neoson_agentcore/sub_agents/governance" \
  "neoson-governance")

INFRA_ARN=$(deploy_agentcore \
  "${REPO_ROOT}/neoson_agentcore/sub_agents/infra" \
  "neoson-infra")

DEV_ARN=$(deploy_agentcore \
  "${REPO_ROOT}/neoson_agentcore/sub_agents/dev" \
  "neoson-dev")

ENDUSER_ARN=$(deploy_agentcore \
  "${REPO_ROOT}/neoson_agentcore/sub_agents/enduser" \
  "neoson-enduser")

RH_ARN=$(deploy_agentcore \
  "${REPO_ROOT}/neoson_agentcore/sub_agents/rh" \
  "neoson-rh")

# ── Step 6: Set sub-agent ARNs in supervisor .agentcore.yaml ─────────────────
echo ""
echo "▶ Updating supervisor .agentcore.yaml with sub-agent ARNs..."
SUP_YAML="${REPO_ROOT}/neoson_agentcore/supervisor/.agentcore.yaml"

# Use Python to update the YAML (avoids sed YAML escaping issues)
python3 - <<PYEOF
import yaml, sys

with open("${SUP_YAML}", "r") as f:
    data = yaml.safe_load(f)

data["environment"]["SUB_AGENT_ARN_GOVERNANCE"] = "${GOVERNANCE_ARN}"
data["environment"]["SUB_AGENT_ARN_INFRA"]      = "${INFRA_ARN}"
data["environment"]["SUB_AGENT_ARN_DEV"]         = "${DEV_ARN}"
data["environment"]["SUB_AGENT_ARN_ENDUSER"]     = "${ENDUSER_ARN}"
data["environment"]["SUB_AGENT_ARN_RH"]          = "${RH_ARN}"

with open("${SUP_YAML}", "w") as f:
    yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

print("Supervisor .agentcore.yaml updated")
PYEOF

# ── Step 7: Deploy Supervisor ─────────────────────────────────────────────────
SUPERVISOR_ARN=$(deploy_agentcore \
  "${REPO_ROOT}/neoson_agentcore/supervisor" \
  "neoson-supervisor")

# ── Step 8: Retrieve API Gateway URL and print summary ────────────────────────
API_URL=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_PREFIX}-foundation" \
  --region "${AWS_REGION}" \
  --query "Stacks[0].Outputs[?OutputKey=='NeosonApiUrl'].OutputValue" \
  --output text 2>/dev/null || echo "N/A")

echo ""
echo "═══════════════════════════════════════════════════"
echo "  ✅  Neoson Deploy Complete"
echo ""
echo "  Supervisor ARN : ${SUPERVISOR_ARN}"
echo "  API Gateway    : ${API_URL}"
echo ""
echo "  Sub-agent ARNs:"
echo "    governance : ${GOVERNANCE_ARN}"
echo "    infra      : ${INFRA_ARN}"
echo "    dev        : ${DEV_ARN}"
echo "    enduser    : ${ENDUSER_ARN}"
echo "    rh         : ${RH_ARN}"
echo ""
echo "  Next steps:"
echo "    1. Configure Knowledge Base IDs in each .agentcore.yaml"
echo "    2. Run: agentcore deploy --env ${ENVIRONMENT} (in supervisor/)"
echo "    3. Send a test request:"
echo "       curl -H 'Authorization: Bearer \$TOKEN' \\"
echo "            -d '{\"mensagem\": \"Qual meu saldo de férias?\"}' \\"
echo "            ${API_URL}/invoke"
echo "═══════════════════════════════════════════════════"
