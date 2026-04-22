#!/usr/bin/env bash
# infrastructure/scripts/build_push_ecr.sh
#
# Build all Neoson container images and push them to Amazon ECR.
#
# USAGE:
#   ./build_push_ecr.sh [ENVIRONMENT] [AWS_ACCOUNT_ID] [AWS_REGION]
#
# EXAMPLES:
#   ./build_push_ecr.sh dev 123456789012 us-east-1
#   ENVIRONMENT=prod ./build_push_ecr.sh
#
# PREREQUISITES:
#   - AWS CLI v2 configured with credentials that have ECR push permissions
#   - Docker Desktop (or colima) running
#   - Repositories already exist in ECR (created by deploy.sh stack 00 or manually)
#
set -euo pipefail

ENVIRONMENT="${1:-${ENVIRONMENT:-dev}}"
AWS_ACCOUNT_ID="${2:-${AWS_ACCOUNT_ID:?'Set AWS_ACCOUNT_ID env var or pass as $2'}}"
AWS_REGION="${3:-${AWS_REGION:-us-east-1}}"
ECR_BASE="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

# Workspace root (two levels up from scripts/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

echo "═══════════════════════════════════════════════════"
echo "  Neoson ECR Build & Push"
echo "  Environment : ${ENVIRONMENT}"
echo "  Account     : ${AWS_ACCOUNT_ID}"
echo "  Region      : ${AWS_REGION}"
echo "  ECR Base    : ${ECR_BASE}"
echo "═══════════════════════════════════════════════════"

# ── ECR login ────────────────────────────────────────────────────────────────
echo ""
echo "▶ Logging in to ECR..."
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${ECR_BASE}"

# ── Helper: build + tag + push one image ─────────────────────────────────────
build_and_push() {
  local CONTEXT_DIR="$1"    # directory containing Dockerfile
  local REPO_NAME="$2"      # e.g. "neoson/supervisor"
  local IMAGE_TAG="${ENVIRONMENT}"

  local FULL_URI="${ECR_BASE}/${REPO_NAME}:${IMAGE_TAG}"
  local LATEST_URI="${ECR_BASE}/${REPO_NAME}:latest"

  echo ""
  echo "▶ Building ${REPO_NAME}:${IMAGE_TAG}"

  # Create ECR repo if it doesn't exist
  aws ecr describe-repositories --repository-names "${REPO_NAME}" \
    --region "${AWS_REGION}" > /dev/null 2>&1 \
    || aws ecr create-repository \
        --repository-name "${REPO_NAME}" \
        --region "${AWS_REGION}" \
        --image-scanning-configuration scanOnPush=true \
        --encryption-configuration encryptionType=AES256 \
        > /dev/null

  # Build (use BuildKit for cache layers)
  DOCKER_BUILDKIT=1 docker build \
    --platform linux/arm64 \
    --build-arg ENVIRONMENT="${ENVIRONMENT}" \
    -t "${FULL_URI}" \
    -t "${LATEST_URI}" \
    -f "${CONTEXT_DIR}/Dockerfile" \
    "${REPO_ROOT}"

  echo "▶ Pushing ${FULL_URI}"
  docker push "${FULL_URI}"
  docker push "${LATEST_URI}"
  echo "✓ ${REPO_NAME} pushed"
}

# ── Supervisor ────────────────────────────────────────────────────────────────
build_and_push \
  "${REPO_ROOT}/neoson_agentcore/supervisor" \
  "neoson/supervisor"

# ── Sub-agents ────────────────────────────────────────────────────────────────
for AGENT in governance infra dev enduser rh; do
  build_and_push \
    "${REPO_ROOT}/neoson_agentcore/sub_agents/${AGENT}" \
    "neoson/${AGENT}"
done

# ── Lambda containers (if using container images instead of ZIPs) ─────────────
# Uncomment if you choose container packaging for Lambdas:
# build_and_push "${REPO_ROOT}/lambdas/ti/dispatcher"    "neoson/lambda-ti"
# build_and_push "${REPO_ROOT}/lambdas/rh/dispatcher"    "neoson/lambda-rh"
# build_and_push "${REPO_ROOT}/lambdas/track_b/query_data_lake" "neoson/lambda-datalake"

echo ""
echo "═══════════════════════════════════════════════════"
echo "  ✅  All images pushed successfully"
echo "═══════════════════════════════════════════════════"
