# social-publish-service

FastAPI service for FB/IG organic publishing v1.

Scope:
- Instagram Feed single image and Carousel.
- Facebook Page image posts.
- No Reels, Stories, Groups, cold DM, or Meta Ads actions.

Safety defaults:
- `SOCIAL_PUBLISH_COMMIT_ENABLED=false` blocks all real publishing.
- `SOCIAL_GENERATION_WRITEBACK_ENABLED=false` blocks generated caption/image prompt writeback to Feishu.
- `SOCIAL_IMAGE_TASK_WRITE_ENABLED=false` blocks writing Codex Image Worker tasks.
- `SOCIAL_IMAGE_RESULT_WRITEBACK_ENABLED=false` blocks image result writeback to the content calendar.
- `SOCIAL_ASSET_PREPARE_ENABLED=false` blocks `file_token` -> Meta CDN URL preparation.
- tokens are read only from environment variables.
- content generation defaults to deterministic template mode when no AI key is configured.
- `/publish/dry-run` runs validation without posting.
- `/publish/commit` requires Base status `待发布`, approval, final asset confirmation, frequency pass, account enabled, mode `auto`, and commit gate enabled.

Generation endpoint:
- `POST /generate/brief`
- Reads one content calendar record.
- Generates `AI生成Brief`, `Hook假设`, `Caption EN`, `Hashtag EN`, `中文说明`, `AI图片Prompt`, `发布Checklist`, `风险Checklist`.
- Blocks invalid generated output before writeback: required generated fields must be non-empty, hashtags must contain `#`, `risk_level` must be `normal`/`high-risk`/`blocked`, captions must not contain configured IP/competitor block terms, and image prompts must include no-text/no-logo guards without asking to render logos, text overlays, or watermarks.
- If `write_back=true` and `SOCIAL_GENERATION_WRITEBACK_ENABLED=true`, writes generated fields to Feishu and moves `状态=选题中` to `待审核`.
- If `write_back=true` while the gate is disabled, returns `GENERATION_WRITEBACK_DISABLED` and does not generate or write content fields.
- It never sets `审批通过`, `最终素材确认`, or `待发布`.
- `文案人工锁定` and `图片Prompt人工锁定` prevent overwriting operator-edited fields.

Generation scan:
- `POST /generate/scan`
- Service-side scan of the content calendar.
- Selects records with `状态=选题中/待审核`, generation status empty/`待生成`/`生成失败`, required inputs present, and not both manually locked.
- Calls the same generation path per selected record.
- This is the preferred n8n cron entry because n8n only triggers one service endpoint and does not need to manage Feishu record filtering.
- Returns `scan_run_id=gscanv1-*` and writes a scan-level summary log when Feishu logging is configured, including zero-candidate scans.
- Each item in `results` includes the source `record_id`, so failed samples and notifications can point back to one Base record.

Image task bridge:
- `POST /image-task/create` builds one Codex Image Worker task payload from a content calendar record.
- `POST /image-task/scan` selects records with `AI图片Prompt`, `图片生成模式=Codex Image`, and no existing `图片任务record_id`, then calls the same task builder.
- `POST /image-task/ingest` maps a worker result back to content fields such as `图片生成状态`, `生成图片file_token`, and `AI生成图链接`.
- `POST /image-task/ingest-scan` selects content records that already have `图片任务record_id` but do not yet have a complete image result, then calls the same ingest path per record.
- The Codex Image Worker remains a local POC. The service only creates/reads Feishu task records after explicit gates are opened.
- Feishu Drive folder URLs are not treated as publishable FB/IG image URLs. Prefer `生成图片file_token`; commit can convert it to a Meta-hosted URL only when `SOCIAL_ASSET_PREPARE_ENABLED=true`.

Replay:
- `POST /replay`
- `run_id` prefix controls routing.
- `gscanv1-*` identifies a scan-level run. Re-run the same scan entrypoint with `POST /generate/scan`; it is an audit summary, not a single-record replay.
- `genv1-*` replays content generation through `/generate/brief` with `source=replay`, `force=true`, and `write_back=false`.
- `imgtaskv1-*` replays image task payload creation as dry-run only.
- `imgresultv1-*` replays image result ingestion as dry-run only.
- `spv1-*` replays publish validation; `mode=commit` is still gated by record state, account config, Meta env, and `SOCIAL_PUBLISH_COMMIT_ENABLED`.
- Generation replay is dry-run only. It cannot approve, confirm assets, publish, or write fields back.

Required environment for production:
- `SOCIAL_PUBLISH_API_TOKEN`
- `SOCIAL_GENERATION_WRITEBACK_ENABLED`
- `SOCIAL_IMAGE_TASK_WRITE_ENABLED`
- `SOCIAL_IMAGE_RESULT_WRITEBACK_ENABLED`
- `SOCIAL_ASSET_PREPARE_ENABLED`
- `SOCIAL_PUBLISH_COMMIT_ENABLED`
- `META_ACCESS_TOKEN`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_BASE_TOKEN`
- `FEISHU_CONTENT_TABLE_ID`
- `FEISHU_ACCOUNT_TABLE_ID`
- `FEISHU_LOG_TABLE_ID`
- `FEISHU_METRICS_TABLE_ID`
- `FEISHU_IMAGE_TASK_BASE_TOKEN`
- `FEISHU_IMAGE_TASK_TABLE_ID`
- `GENERATION_AI_PROVIDER` (`template` by default; use an OpenAI-compatible provider name for live AI)
- `GENERATION_AI_BASE_URL`
- `GENERATION_AI_API_KEY`
- `GENERATION_AI_MODEL`
- `GENERATION_AI_TIMEOUT_SECONDS`

Local check:

```bash
python -m unittest discover -s tests
python scripts/smoke_generate_scan.py
uvicorn app.main:app --reload --port 8080
```

Full local preflight from repo root:

```powershell
powershell -ExecutionPolicy Bypass -File tools\fb_ig_ops\preflight_content_generation_chain.ps1
```

This preflight checks unit tests, local generation smoke, Python compile, n8n draft JSON for content generation, image task, image result ingest, and publishing, inactive workflow status, expected service endpoints, image worker handoff, Base schema shape, sensitive-looking strings, and then clears Python cache.

Smoke against a running service, still without writing Feishu:

```bash
python scripts/smoke_generate_scan.py --url http://127.0.0.1:8080
```

Remote readiness before opening generation writeback:

```bash
python scripts/remote_generation_readiness.py --url https://<service-domain>
# or set SOCIAL_PUBLISH_SERVICE_URL and run:
python scripts/remote_generation_readiness.py
```

This checks `/health`, verifies `SOCIAL_PUBLISH_COMMIT_ENABLED=false`, verifies the generation/image write gates and asset preparation gate are closed, requires Feishu env to be configured for service-side Base scan, runs inline `/generate/scan`, `/image-task/scan`, and `/image-task/ingest-scan` dry-runs, and confirms `write_back=true` is blocked by `GENERATION_WRITEBACK_DISABLED`. Use `--allow-feishu-unconfigured` only for an inline-only smoke test before Feishu env is attached.

Optional evidence file:

```bash
python scripts/remote_generation_readiness.py --url https://<service-domain> --report-path outputs/fb_ig_ops/remote_generation_readiness.json
```

Local release gate using that evidence file:

```bash
python ../../tools/fb_ig_ops/release_gate_content_generation.py --readiness-report ../../outputs/fb_ig_ops/remote_generation_readiness.json
```

Before using live AI generation in observation, require AI config as well:

```bash
python scripts/remote_generation_readiness.py --url https://<service-domain> --expect-ai-configured
python ../../tools/fb_ig_ops/release_gate_content_generation.py --readiness-report ../../outputs/fb_ig_ops/remote_generation_readiness.json --expect-ai-configured
```

Single persisted record check:

```bash
# dry-run one Feishu record through /generate/brief
python scripts/smoke_generate_scan.py --url https://<service-domain> --record-id rec_xxx --force

# write-back is allowed only when record_id is explicit
python scripts/smoke_generate_scan.py --url https://<service-domain> --record-id rec_xxx --write-back --force
```

The script never prints tokens. `--write-back` cannot run on inline fixtures, so the default smoke test cannot modify production data.
