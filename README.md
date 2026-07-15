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
- `SOCIAL_APPROVAL_WRITEBACK_ENABLED=false` blocks approval-card callbacks from writing operator decisions back to Feishu.
- tokens are read only from environment variables.
- content generation defaults to deterministic template mode when no AI key is configured.
- `/publish/dry-run` runs validation without posting.
- `/publish/commit` requires Base status `待发布`, approval, final asset confirmation, frequency pass, account enabled, mode `auto`, and commit gate enabled.

Generation endpoint:
- `POST /generate/brief`
- Reads one content calendar record.
- Generates `AI生成Brief`, `Hook假设`, `Caption EN`, `Hashtag EN`, `中文说明`, `AI图片Prompt`, `发布Checklist`, `风险Checklist`.
- Before generation, best-effort enriches the content record from the product library by `产品库记录ID` or exact SKU/brand-model match. The enriched context includes product reference images, product brief, brand/model names, and FUNLAB IP compliance fields.
- Blocks invalid generated output before writeback: required generated fields must be non-empty, hashtags must contain `#`, `risk_level` must be `normal`/`high-risk`/`blocked`, captions must not contain configured IP/competitor block terms, and image prompts must include product-reference source-of-truth wording plus no-text/no-new-logo-overlay guards without asking to render new logos, text overlays, watermarks, or product redesigns.
- For FUNLAB Codex Image content, `IP合规状态` / `产品库IP合规状态` must be `合规-无IP` or `合规-已授权`; empty or risky statuses block generation.
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
- `POST /image-task/scan` selects records with `AI图片Prompt`, `图片生成模式=Codex Image`, product reference image available, and no existing `图片任务record_id`, then calls the same task builder.
- Codex Image tasks now carry three explicit reference roles:
  - `设计参考图`: competitor/creator/social reference for scene, composition, mood, lighting, and camera only. It must not change the product or copy logos/text/brand marks.
  - `产品参考图包`: product source-of-truth pack for product shape, proportions, color, material, buttons, ports, textures, visible markings, and accessory layout.
  - `细节参考图`: detail override pack for small buttons, icons, ports, shell pattern, and other high-risk product-fidelity details.
- `参考图使用策略` defaults to `产品保真优先`. If references conflict, `产品参考图包` and `细节参考图` override `设计参考图`.
- The service still writes legacy `产品原图` with the same attachments as `产品参考图包` so old deployment workers remain compatible.
- `POST /image-task/ingest` maps a worker result back to content fields such as `图片生成状态`, `生成图片file_token`, and `AI生成图链接`.
- `POST /image-task/ingest-scan` selects content records that already have `图片任务record_id` but do not yet have a complete image result, then calls the same ingest path per record.
- The Codex Image Worker is an external deployment-terminal consumer, normally bound to the Feishu image task table. This service only creates/reads Feishu task records after explicit gates are opened, and current-machine worker deployment is not required.
- Pending worker states such as `待处理`, `已提交`, `生成中`, `处理中`, `pending`, and `running` are treated as pending no-ops by `/image-task/ingest-scan`, not as scheduler failures.
- Feishu Drive folder URLs are not treated as publishable FB/IG image URLs. Prefer `生成图片file_token`; commit can convert it to a Meta-hosted URL only when `SOCIAL_ASSET_PREPARE_ENABLED=true`.

Approval callback:
- `POST /approval/card-preview` returns a card payload model for one content calendar record.
- `POST /approval/action` maps operator decisions to content-calendar field updates.
- Supported actions: `approve_schedule`, `regenerate_image`, `regenerate_copy`, `regenerate_both`, `reject`.
- Image regeneration feedback v3 uses 12 single-select dimensions in `feedback_dimensions`, mirrored to content-calendar fields `图片反馈-*`: product fidelity, composition, camera angle, depth layers, scene type, background/surface, props, lighting, color palette, style, platform fit, and risk control.
- Copy feedback can include `copy_overrides` with `caption_en` and/or `hashtag_en`. When present, `regenerate_copy` writes the operator-edited `Caption EN` / `Hashtag EN` directly, sets `文案人工锁定=true`, and does not mark `AI生成状态=待生成`. Without overrides, it keeps the old AI regeneration flow through `文案修改意见`.
- `regenerate_both` can combine image feedback and copy overrides: image fields still move to `图片生成状态=待生成`; changed copy fields are written directly and locked.
- Legacy `feedback_tags` are still accepted for older callbacks, but `/approval/card-preview` no longer exposes flat feedback buttons.
- FUNLAB hidden/emissive shell linework is a server-side brand hard rule, not an operator-selectable issue. For FUNLAB records the patch automatically adds `FUNLAB_HIDDEN_EMISSIVE_PATTERN` to keep teal contour graphics as embedded luminous linework, not flat printed graphics.
- The endpoint returns dry-run updates by default. Real Feishu writeback requires both `write_back=true` and `SOCIAL_APPROVAL_WRITEBACK_ENABLED=true`.
- Regenerating an image clears stale image task/result fields and moves `图片生成状态=待生成`; it does not approve, confirm final assets, or publish.
- When `create_image_task=true`, image regeneration also builds the next Codex Image task from the patched fields. With `write_back=false`, the image task is dry-run only and returned in the response. With `write_back=true`, the task is written only if `SOCIAL_IMAGE_TASK_WRITE_ENABLED=true`.
- `scripts/send_approval_card_v3.py --send` sends a real v3 Feishu whole-post review card through the event-hub app. It reuses `IMAGE_FEEDBACK_DIMENSIONS`, uploads the design-reference/current generated images as IM images, displays the current `Caption EN` / `Hashtag EN`, and includes editable `caption_en_override` / `hashtag_en_override` inputs. Product reference images are for internal image-generation source-of-truth, not the operator comparison slot.
- The card button value carries `original_caption_en` / `original_hashtag_en`; n8n compares submitted input values against those originals and only sends changed text in `copy_overrides`. This prevents prefilled inputs from locking unchanged copy accidentally.
- Feishu form cards must put `form_submit` buttons directly under `form.elements`. Do not wrap submit buttons in an `action` container inside a form, because the Feishu client can silently hide the whole form.

Weekly planning and discovery:
- `POST /plan/weekly-input-card` returns the Monday four-account strategy card payload for FUNLAB FB, FUNLAB IG, POWKONG FB, and POWKONG IG. n8n sends the card; the service does not send Feishu IM by itself.
- `POST /plan/weekly-input-action` locks weekly strategies from operator submissions. Empty submissions fall back to defaults. Product pool input is resolved through `社媒产品索引缓存` first, with ERP SKU / brand-model text as fallback.
- `POST /plan/product-index/sync` builds product dropdown rows from product-library records. Writeback is gated by `SOCIAL_PLAN_WRITEBACK_ENABLED=true`.
- `POST /discovery/reference/weekly` creates AI weekly competitor/design reference candidates in `待确认` state. Only references with `状态=可用` are eligible for `/plan/weekly`.
- `POST /discovery/kol/weekly` returns KOL image-reference review cards for candidates that pass the IG/FB image-post gate. This card is for selecting reusable scene/composition/light/product-slot references, not for approving KOL collaborations or posts. Instagram account homepages, YouTube links, Reels/video pages, generic websites, and obvious placeholder URLs are excluded; if no candidate passes, the endpoint returns zero cards instead of fabricating weak references. The weekly endpoint also accepts `visual_posts` plus `min_visual_score`, so n8n or a discovery agent can repush a KOL card through the existing weekly branch after collecting IG/FB image-post URLs and screenshots. When `prepare_image_keys=true`, the service downloads the thumbnail/screenshot URL, uploads it to Feishu IM, and returns card `img` elements with `样例帖子1图片Key / 样例帖子2图片Key`.
- `POST /discovery/kol/visual-posts` scores already-collected public IG/FB image-post candidates. Ready candidates must include a post-level IG/FB image-post URL plus a thumbnail/screenshot URL. For production review cards, pass `prepare_image_keys=true` so the service prepares Feishu `image_key` values and embeds the preview image directly; callers may also pass existing `thumbnail_image_key` / `screenshot_image_key`. Weak links are returned in `rejected` with the reason. This endpoint is the stable entry for later discovery agents or operator-supplied post lists, not a login-wall crawler. The visual gate is image-first: reject selfie/portrait/creator-face/person-first posts even if the account is gaming-related; accept hands-only, product-dominant, flat-lay, desk setup, TV-background, close-up, and other posts where the product slot can be replaced by FUNLAB/POWKONG.
- `POST /discovery/kol/action` handles per-account feedback. `approve` writes a usable `博主图片帖` reference with `账号/帖子URL`, `视觉参考缩略图`, `样例图片链接`, `图片帖合格性`, and `图片帖合格原因`; `reject_replace` returns the same number of replacement candidates; `hold` and `block_similar` only update the candidate state.
- Feishu cards that need button callbacks must be sent by the event-hub app (`FEISHU_EVENT_APP_ID`, 聪哥分身3号), not the default `FEISHU_APP_ID` / 聪哥分身1号. The event-hub bot must also be a member of the target group; otherwise Feishu send returns `230002 Bot/User can NOT be out of the chat`. If a KOL card is sent by the wrong app, Feishu shows "该应用尚未配置卡片回调" before the request reaches n8n. Use `scripts/send_kol_visual_card_event_app.py --send` for manual KOL visual-card resend tests.
- Bitable writeback uses `FEISHU_BITABLE_APP_ID` / `FEISHU_BITABLE_APP_SECRET` first, with `FEISHU_APP_ID` as fallback. KOL card-only fields such as `样例帖子1图片Key` are not written to Base; empty URL fields are omitted before writeback to avoid `URLFieldConvFail`.
- All discovery and strategy writebacks use the same plan gate: `write_back=true` is blocked unless `SOCIAL_PLAN_WRITEBACK_ENABLED=true`.
- Base tables:
  - `账号内容策略表`: `tblyavkR6xdEt9gd`
  - `参考对象库`: `tblMDwMv07jbeGAE` (`参考类型` includes `博主图片帖`; image-post QA fields: `视觉参考缩略图`, `样例图片链接`, `图片帖合格性`, `图片帖合格原因`)
  - `周候选池`: `tblV8rGXyRWGsE5r`
  - `社媒产品索引缓存`: `tblfI565xItYpXhE`
  - `KOL参考候选池`: `tblAIDN2cMSQVgGR`

Replay:
- `POST /replay`
- `run_id` prefix controls routing.
- `gscanv1-*` identifies a scan-level run. Re-run the same scan entrypoint with `POST /generate/scan`; it is an audit summary, not a single-record replay.
- `genv1-*` replays content generation through `/generate/brief` with `source=replay`, `force=true`, and `write_back=false`.
- `imgtaskv1-*` replays image task payload creation as dry-run only.
- `imgresultv1-*` replays image result ingestion as dry-run only.
- `spv1-*` replays publish validation; `mode=commit` is still gated by record state, account config, Meta env, and `SOCIAL_PUBLISH_COMMIT_ENABLED`.
- Generation replay is dry-run only. It cannot approve, confirm assets, publish, or write fields back.

Operational fixes:
- 2026-07-15 FB/IG daily confirm writeback:
  - Problem: Feishu daily confirm card clicks returned failure cards and did not create content-calendar rows.
  - Root cause: Base datetime fields rejected numeric millisecond strings, number fields could receive string values, and legacy weekly-pool pillar `UGC` did not match the content-calendar option `UGC/KOL社证`.
  - Additional root cause found during replay: weekly-pool writeback attempted to write content-calendar-only fields `日确认动作` and `日确认时间`, which do not exist in the weekly-pool table.
  - Fix: Bitable normalization now converts numeric datetime strings and numeric strings; weekly-pool `UGC` is mapped to `UGC/KOL社证`; weekly-pool writeback is filtered to fields that exist in that table; Feishu writeback failures return a readable `FEISHU_WRITEBACK_FAILED` detail instead of an opaque 500.
  - Related n8n fix: workflow `YjTXaoWAcy89xZpT` node `FBIG Daily Confirm Reply` now stringifies object errors so failure cards no longer show `[object Object]`.
  - Verification: full local `unittest discover -s tests` passed 93 tests; Zeabur deployment from commit `7fdbb6b52ae0c7dc552991b713fc1c27a796782b` reached `RUNNING`; production `/plan/reselect` dry-run returned `UGC/KOL社证`; production writeback smoke created content-calendar record `recvpqo3NoSsxh` with datetime and number fields normalized, then the test record was deleted.
  - Repair result: the three failed operator clicks were replayed after the fix. Weekly candidates `recvpekE9WTW10`, `recvpekFAUhUgq`, and `recvpekGWmMeCM` now have `日确认状态=已生成内容日历` and content-calendar records `recvpqrjw9m6vm`, `recvpqrmcpCqNy`, and `recvpqronWbHBx`.
- 2026-07-15 FB/IG generation scan readiness gate:
  - Problem: after the daily-confirm repair, `/generate/scan` alerted with `GENERATION_OUTPUT_INVALID` for the three replayed records because they used placeholder products (`FUNLAB hero product` / `Powkong hero product`) and lacked product reference images; FUNLAB records also lacked IP compliance status.
  - Fix: weekly planning no longer fabricates `{brand} hero product` candidates when product pools are empty, and `generate/scan` skips records missing required product reference images or FUNLAB IP compliance instead of selecting them and reporting a generation failure.
  - Verification: full local `unittest discover -s tests` passed 95 tests; Zeabur deployment from commit `cb001753c678307ea98e606f6ae480a171a92fb9` reached `RUNNING`; production replay `POST /generate/scan {"write_back":false,"source":"replay","force":false,"limit":3}` returned `ok=true`, `selected=0`, `failed=0`, with the three replayed records skipped as `missing_product_reference_image`.

Required environment for production:
- `SOCIAL_PUBLISH_API_TOKEN`
- `SOCIAL_GENERATION_WRITEBACK_ENABLED`
- `SOCIAL_IMAGE_TASK_WRITE_ENABLED`
- `SOCIAL_IMAGE_RESULT_WRITEBACK_ENABLED`
- `SOCIAL_ASSET_PREPARE_ENABLED`
- `SOCIAL_APPROVAL_WRITEBACK_ENABLED`
- `SOCIAL_PUBLISH_COMMIT_ENABLED`
- `META_ACCESS_TOKEN`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_BASE_TOKEN`
- `FEISHU_CONTENT_TABLE_ID`
- `FEISHU_ACCOUNT_TABLE_ID`
- `FEISHU_STRATEGY_TABLE_ID`
- `FEISHU_REFERENCE_TABLE_ID`
- `FEISHU_WEEKLY_POOL_TABLE_ID`
- `FEISHU_WEEKLY_REVIEW_TABLE_ID`
- `FEISHU_PRODUCT_INDEX_TABLE_ID`
- `FEISHU_KOL_CANDIDATE_TABLE_ID`
- `FEISHU_LOG_TABLE_ID`
- `FEISHU_METRICS_TABLE_ID`
- `FEISHU_IMAGE_TASK_BASE_TOKEN`
- `FEISHU_IMAGE_TASK_TABLE_ID`
- `FEISHU_PRODUCT_LIBRARY_BASE_TOKEN`
- `FEISHU_PRODUCT_POWKONG_TABLE_ID`
- `FEISHU_PRODUCT_FUNLAB_TABLE_ID`
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
