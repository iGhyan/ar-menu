# ar-assets-lambda

API Gateway REST Lambda for AR model asset management.

## Routes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/ar/{itemId}` | Presigned S3 URL (TTL 900 s) for `.glb` download |
| PUT | `/ar/{itemId}` | Upsert AR metadata on MenuTable (`arModelKey`, `arScale`, `arPlacement`) |
| DELETE | `/ar/{itemId}` | Remove AR metadata + CloudFront invalidation |

## Environment variables

| Variable | Description |
|----------|-------------|
| `BUCKET_AR` | S3 bucket holding `.glb` models |
| `CF_DOMAIN` | CloudFront domain e.g. `d1234.cloudfront.net` |
| `TABLE_MENU` | DynamoDB MenuTable name |

## Error codes

| HTTP | Reason |
|------|--------|
| 400 | Missing `itemId` / invalid JSON / no valid AR fields |
| 403 | S3 `AccessDenied` on presign |
| 404 | Item not found / AR model not configured |
| 405 | Unsupported HTTP method |
| 500 | DynamoDB or S3 internal error |

> CloudFront invalidation failure is **non-critical** — logged as WARNING, response still `200`.

## Run tests

```bash
pip install -r requirements.txt
pytest test_handler.py -v --cov=handler --cov-report=term-missing
```

## Deploy (SAM)

```bash
sam build
sam deploy --parameter-overrides \
  BucketAr=my-ar-bucket \
  CfDomain=d1234abcd.cloudfront.net \
  TableMenu=MenuTable
```