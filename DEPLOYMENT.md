# Deployment & Setup Guide

## Prerequisites

- Docker and Docker Compose installed
- A Meta Developer App with Ads Library API access confirmed
- A long-lived Meta User Access Token
- An OpenRouter API key

---

## 1. Clone and Configure

```bash
git clone <your-repo-url>
cd ad-intelligence

cp .env.example .env
```

Edit `.env` and fill in your credentials:

```bash
nano .env
```

Required values:

```
META_ACCESS_TOKEN=your_long_lived_token_here
OPENROUTER_API_KEY=your_openrouter_key_here
```

The Meta access token must belong to a Facebook account that has completed the Ads Library API identity verification at `facebook.com/ads/library/api`. A standard developer token is not sufficient — the account-level confirmation must be completed separately.

---

## 2. Build and Start

```bash
docker compose up --build -d
```

This starts four containers: `api`, `worker`, `postgres`, `valkey`.

Verify all are running:

```bash
docker compose ps
```

All four should show `Up`. If the worker shows `Exited`, check logs:

```bash
docker compose logs worker --tail=30
```

---

## 3. Run Database Migrations

```bash
docker compose exec api alembic upgrade head
```

Verify the schema:

```bash
docker compose exec postgres psql -U adint -d adint -c "\dt"
```

Should show: `brands`, `ads`, `insights`, `jobs`.

---

## 4. Verify the API is Running

```bash
curl http://localhost:8000/api/v1/health | jq
```

Expected: `{"status": "healthy", "service": "ad-intelligence"}`

Swagger docs are available at: `http://localhost:8000/docs`

---

## 5. Full Workflow: From Brand to Insights

### Step 1 — Fetch ads for a brand

```bash
curl -X POST http://localhost:8000/api/v1/brands/search \
  -H "Content-Type: application/json" \
  -d '{
    "identifier": "15087023444",
    "identifier_type": "page_id",
    "countries": ["GB", "DE", "FR"],
    "ad_active_status": "ALL",
    "max_ads": 50
  }' | jq
```

Save the `job_id` from the response.

> **Note on countries:** Use EU countries (`GB`, `DE`, `FR`, `NL`) to maximize the chance of getting impression and reach data. The Meta Ads Library API only returns these metrics for EU-delivered ads under GDPR transparency rules. US-only queries will return ads but without performance data.

> **Note on page IDs:** Use the numeric page ID, not the brand name. Brand names may match multiple pages. You can find a page's ID from their Facebook URL or the Ads Library UI.

### Step 2 — Poll until the fetch job completes

```bash
# Replace JOB_ID with the value from Step 1
watch -n 3 'curl -s http://localhost:8000/api/v1/jobs/JOB_ID/status | jq "{status, result}"'
```

Press `Ctrl+C` when status shows `DONE`. A typical fetch of 50 ads takes 1–3 minutes depending on snapshot parsing speed.

### Step 3 — Check what was fetched

```bash
# List all brands
curl http://localhost:8000/api/v1/brands | jq '.brands[] | {id, page_name, ad_count}'

# List ads for the brand (replace BRAND_ID)
curl "http://localhost:8000/api/v1/ads?brand_id=BRAND_ID&limit=10" | jq \
  '.ads[] | {id, ad_archive_id, is_active, ad_type, performance_label, impressions_mid}'
```

### Step 4 — Find scoreable inactive ads

```bash
# Ads with performance labels are those where impression + reach data was available
curl "http://localhost:8000/api/v1/ads?brand_id=BRAND_ID&status=INACTIVE&sort_by=impressions_mid&order=desc&limit=10" | jq \
  '.ads[] | select(.performance_label != null) | {id, performance_label, impressions_mid, reach_mid}'
```

### Step 5 — Generate insight for a specific ad

```bash
# Replace AD_ID with an id from Step 4
curl -X POST "http://localhost:8000/api/v1/ads/AD_ID/insights/generate" | jq
```

Save the `job_id` from this response.

### Step 6 — Poll insight job

```bash
watch -n 3 'curl -s http://localhost:8000/api/v1/jobs/INSIGHT_JOB_ID/status | jq .status'
```

Insight generation takes 15–60 seconds depending on the model. Press `Ctrl+C` when status shows `DONE`.

### Step 7 — Fetch the generated insight

```bash
curl "http://localhost:8000/api/v1/ads/AD_ID/insights" | jq '{
  summary,
  analysis_mode,
  factors: [.factors[] | {trait, category, impact, confidence, evidence}]
}'
```

---

## 6. Useful Management Commands

### View live worker logs

```bash
docker compose logs worker -f
```

### View live API logs

```bash
docker compose logs api -f
```

### Stop everything gracefully

```bash
docker compose stop
```

### Restart after code changes

```bash
docker compose up --build -d
```

### Check metrics

```bash
curl http://localhost:8000/api/v1/metrics/summary | jq
```

### Delete an insight to regenerate it

```bash
curl -X DELETE "http://localhost:8000/api/v1/ads/AD_ID/insights"
# Then POST to /generate again
```

### Clear bad media paths (if snapshot parsing saved HTML as image)

```bash
docker compose exec postgres psql -U adint -d adint -c \
  "UPDATE ads SET media_local_path = NULL WHERE brand_id = 'BRAND_ID';"
```

### Run migrations after schema changes

```bash
docker compose exec api alembic upgrade head
```

### Reset everything (WARNING: deletes all data)

```bash
docker compose down -v
docker compose up --build -d
docker compose exec api alembic upgrade head
```

---

## 7. Troubleshooting

**Worker container exits immediately**
Check for import errors: `docker compose logs worker --tail=20`

**Job stays PENDING and never runs**
Check if the worker is running: `docker compose ps`
Check Valkey queue depth: `docker compose exec valkey valkey-cli LLEN jobs:pending`

**Meta API returns error 10 (no permission)**
The Facebook account needs to complete identity verification at `facebook.com/ads/library/api` — this is separate from having a developer app.

**Insights fail with `'choices'` error**
The OpenRouter API returned an error instead of a completion. Check your API key and model availability:

```bash
curl https://openrouter.ai/api/v1/models \
  -H "Authorization: Bearer $(grep OPENROUTER_API_KEY .env | cut -d= -f2)" | \
  jq '.data[] | select(.id | contains("qwen-vl")) | .id'
```

**All ads have `performance_label: null`**
The fetched ads have no impression/reach data — most likely because `countries: ["US"]` was used. Re-fetch with EU countries.

**`analysis_mode: text_only` on all insights**
The snapshot HTML parser is not finding image URLs. This is expected — Meta's snapshot pages require authenticated sessions. Text-only analysis using ad copy and performance data will still generate meaningful insights.
