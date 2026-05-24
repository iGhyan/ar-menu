# AWS WebSocket SAM Project

WebSocket API Gateway + 3 Lambda Functions (Python) + ElastiCache Redis + DynamoDB + Step Functions

---

## Project Structure

```
ws-project/
├── template.yaml              # SAM infrastructure (main file)
├── samconfig.toml             # Deploy config (dev/prod)
├── Makefile                   # Shortcut commands
├── requirements-dev.txt       # Local test dependencies
│
├── functions/
│   ├── ws_connect/
│   │   └── handler.py         # $connect — JWT validate + Redis HSET
│   ├── ws_disconnect/
│   │   └── handler.py         # $disconnect — Redis DEL
│   └── ws_message/
│       └── handler.py         # $default — DynamoDB + Step Functions
│
├── layer/
│   └── requirements.txt       # Lambda Layer (redis, PyJWT)
│
├── tests/
│   └── test_handlers.py       # pytest tests (all 3 functions)
│
└── events/
    ├── connect.json           # sam local invoke event
    ├── disconnect.json
    ├── message.json
    └── env.json               # local env vars
```

---

## Prerequisites

```bash
# Install karo
pip install aws-sam-cli
aws configure   # AWS credentials set karo

# SAM version check
sam --version   # 1.100+ chahiye
```

---

## Local Setup & Test

```bash
# Step 1: Dependencies install karo
make install
# ya
pip install -r requirements-dev.txt

# Step 2: Tests chalao
make test

# Step 3: Coverage report ke saath
make test-cov
# Connect >= 80%, Disconnect >= 70%, Message >= 75%
```

---

## AWS Deploy

### First time setup

```bash
# 1. S3 bucket banao (artifact store ke liye)
aws s3 mb s3://your-sam-bucket-dev --region us-east-1

# 2. samconfig.toml mein apni values fill karo:
#    - s3_bucket
#    - RedisEndpoint (ElastiCache hostname)
#    - VpcId, PrivateSubnet1, PrivateSubnet2

# 3. Build + Deploy
make deploy-dev
```

### Subsequent deploys

```bash
make deploy-dev    # dev environment
make deploy-prod   # production
```

---

## Local Lambda Invoke (SAM)

```bash
# Pehle build karo
sam build

# Connect test karo
make local-connect

# Disconnect test karo
make local-disconnect

# Message test karo
make local-message
```

---

## Environment Variables

| Function            | Variable      | Value                              |
|---------------------|---------------|------------------------------------|
| ws-connect-lambda   | REDIS_URL     | redis://your-endpoint:6379         |
| ws-connect-lambda   | TABLE_CONN    | ConnectionTable-dev                |
| ws-connect-lambda   | JWT_SECRET    | (Secrets Manager se lena best hai) |
| ws-disconnect-lambda| REDIS_URL     | redis://your-endpoint:6379         |
| ws-message-lambda   | TABLE_ORDER   | OrderTable-dev                     |
| ws-message-lambda   | STEP_ARN      | arn:aws:states:...:stateMachine:.. |
| ws-message-lambda   | DLQ_URL       | https://sqs.amazonaws.com/...      |

---

## WebSocket Test (wscat)

```bash
# Install wscat
npm install -g wscat

# Connect karo
wscat -c "wss://YOUR-API-ID.execute-api.us-east-1.amazonaws.com/dev" \
      -H "Authorization: Bearer YOUR_JWT_TOKEN"

# Message bhejo
> {"status": "confirmed", "orderId": "ord-001"}
```

---

## Error Handling Summary

| Function       | Scenario              | Response                    |
|----------------|-----------------------|-----------------------------|
| ws-connect     | Invalid/missing JWT   | 401 (WS upgrade rejected)   |
| ws-disconnect  | Redis DEL fail        | 200 (log + continue)        |
| ws-message     | Invalid status        | 400                         |
| ws-message     | Step Functions fail   | 200 + message goes to DLQ   |

---

## Useful Commands

```bash
# Stack status check
aws cloudformation describe-stacks --stack-name ws-app-dev

# Lambda logs dekho
sam logs -n WsConnectFunction --stack-name ws-app-dev --tail

# Stack delete karo
aws cloudformation delete-stack --stack-name ws-app-dev
```
