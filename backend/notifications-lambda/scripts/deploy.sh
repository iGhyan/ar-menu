#!/usr/bin/env bash
# scripts/deploy.sh
# SAM build + deploy for notifications-lambda
# Usage: ./scripts/deploy.sh [dev|staging|prod]
# Requires: aws-cli, sam-cli, jq

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
STAGE="${1:-dev}"
STACK_NAME="notifications-lambda-${STAGE}"
S3_BUCKET="${DEPLOY_BUCKET:-your-sam-deploy-bucket}"   # override via env
REGION="${AWS_REGION:-us-east-1}"
PROFILE="${AWS_PROFILE:-default}"

echo "▶  Stage     : ${STAGE}"
echo "▶  Stack     : ${STACK_NAME}"
echo "▶  Region    : ${REGION}"
echo "▶  S3 Bucket : ${S3_BUCKET}"
echo ""

# ── Validate required env vars ────────────────────────────────────────────────
required_vars=(
  REDIS_URL
  WS_ENDPOINT
  SNS_TOPIC_ARN
  VPC_SUBNET_IDS
  VPC_SECURITY_GROUP_IDS
  ORDER_TABLE_NAME
  WS_CONNECTIONS_TABLE_NAME
)

for var in "${required_vars[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    echo "❌  Missing required env var: ${var}"
    exit 1
  fi
done

# ── Install layer deps ────────────────────────────────────────────────────────
echo "📦  Installing layer dependencies..."
pip install \
  --target layer/python \
  --platform manylinux2014_aarch64 \
  --only-binary=:all: \
  --upgrade \
  redis \
  --quiet

# ── SAM build ─────────────────────────────────────────────────────────────────
echo "🔨  Building..."
sam build \
  --template-file template.yaml \
  --use-container \
  --cached

# ── SAM deploy ────────────────────────────────────────────────────────────────
echo "🚀  Deploying to ${STAGE}..."
sam deploy \
  --stack-name        "${STACK_NAME}" \
  --s3-bucket         "${S3_BUCKET}" \
  --region            "${REGION}" \
  --profile           "${PROFILE}" \
  --capabilities      CAPABILITY_IAM CAPABILITY_AUTO_EXPAND \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    Stage="${STAGE}" \
    RedisUrl="${REDIS_URL}" \
    WsEndpoint="${WS_ENDPOINT}" \
    SnsTopicArn="${SNS_TOPIC_ARN}" \
    OrderTableName="${ORDER_TABLE_NAME}" \
    WsConnectionsTableName="${WS_CONNECTIONS_TABLE_NAME}" \
    PinpointAppId="${PINPOINT_APP_ID:-}" \
    PinpointFromNumber="${PINPOINT_FROM_NUMBER:-}" \
    VpcSubnetIds="${VPC_SUBNET_IDS}" \
    VpcSecurityGroupIds="${VPC_SECURITY_GROUP_IDS}"

# ── Print outputs ─────────────────────────────────────────────────────────────
echo ""
echo "✅  Deploy complete. Stack outputs:"
aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" \
  --region     "${REGION}" \
  --profile    "${PROFILE}" \
  --query      "Stacks[0].Outputs" \
  --output     table