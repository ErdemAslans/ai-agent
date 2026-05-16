# AI Development Agent

> Task management → AI agent → Pull Request automation. Built for Vodafone Senior Developer Challenge.

## Status

🚧 **Work in progress — Day 1 of 5.** Full README at Day 5.

## Quick Preview

```bash
# Setup
cp .env.example .env
# Fill in GEMINI_API_KEY and GITHUB_TOKEN

# Run
docker-compose up -d

# Test (Day 1: returns 202)
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d @examples/task.json

# Check health
curl http://localhost:8000/health/
```

## Plan

See `plans/` directory for the 5-day implementation roadmap:

- [plan.md](plans/plan.md) — Day-by-day breakdown
- [architecture.md](plans/architecture.md) — System architecture
- [agents.md](plans/agents.md) — 5 AI agents detail
- [validators.md](plans/validators.md) — 7-layer validation pipeline
- [security.md](plans/security.md) — Security approach
- [pdf-mapping.md](plans/pdf-mapping.md) — Every feature → PDF requirement

## Tech Stack

- **Backend:** Python 3.12 + Django 5 + DRF
- **Queue:** Celery + Redis
- **Database:** PostgreSQL 15
- **AI:** Google Gemini API (Pro + Flash)
- **Git:** GitPython + PyGithub
- **Sandbox:** Docker SDK
- **Logging:** structlog (JSON to stdout)
