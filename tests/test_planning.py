import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app.discovery as discovery_module
import app.main as main_module
from app.config import Settings
from app.main import app
from app.discovery import resolve_product_pool
from app.planning import build_weekly_candidates, content_calendar_fields_from_candidate


def strategy(**overrides):
    fields = {
        "策略标题": "FUNLAB IG weekly",
        "账号名称": "FUNLAB IG",
        "品牌": "FUNLAB",
        "平台": "Instagram",
        "每周候选数": 2,
        "内容支柱配比": "产品场景\n卖点教育",
        "目标信号优先级": "Saves\nComments",
        "产品池": "FUNLAB FF01A-04 Controller\nFUNLAB YS11 Controller",
        "SEO主题簇": "hidden glow controller\nswitch controller setup",
        "GEO问题池": "What is a hidden glow controller?\nHow do I choose a Switch controller?",
        "Hashtag分层池": "#FUNLAB #GamingController #DeskSetup #HiddenGlow #SetupInspo",
        "状态": "启用",
    }
    fields.update(overrides)
    return {"record_id": "rec_strategy", "fields": fields}


def reference(**overrides):
    fields = {
        "参考标题": "Competitor couch gaming scene",
        "品牌": "FUNLAB",
        "平台": "Instagram",
        "参考类型": "社媒帖子",
        "账号/帖子URL": "https://example.com/ref-post",
        "内容支柱": "产品场景",
        "可借鉴元素": "电视背景、手持视角、低亮度氛围",
        "禁止复制元素": "竞品 logo、产品外观、屏幕 UI",
        "适用品类/产品": "",
        "状态": "可用",
    }
    fields.update(overrides)
    return {"record_id": "rec_ref", "fields": fields}


def product(**overrides):
    fields = {
        "产品库记录ID": "rec_product_funlab_01",
        "品牌": "FUNLAB",
        "产品名": "FUNLAB FF01A-04 Controller",
        "产品英文名": "FUNLAB Firefly Controller",
        "品牌型号": "FF01A-04",
        "ERP SKU": "FLPR008B",
        "下拉显示名": "FUNLAB FF01A-04 Controller / FF01A-04 / FLPR008B",
        "检索关键词": "FUNLAB FF01A-04 FLPR008B Firefly Controller",
    }
    fields.update(overrides)
    return {"record_id": "rec_product_index", "fields": fields}


def account(**overrides):
    fields = {
        "账号名称": "FUNLAB IG",
        "品牌": "FUNLAB",
        "平台": "Instagram",
        "每周上限": 3,
        "默认产品池": "FF01A-04",
        "本周业务重点": "测试手柄场景图",
    }
    fields.update(overrides)
    return {"record_id": "rec_account", "fields": fields}


class PlanningTest(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def test_feishu_base_client_prefers_bitable_app_identity(self):
        settings = Settings(
            feishu_app_id="default-app",
            feishu_app_secret="default-secret",
            feishu_bitable_app_id="bitable-app",
            feishu_bitable_app_secret="bitable-secret",
            feishu_base_token="base",
        )
        client = main_module._feishu(settings)
        self.assertIsNotNone(client)
        self.assertEqual(client.app_id, "bitable-app")
        self.assertEqual(client.app_secret, "bitable-secret")

    def test_build_weekly_candidates_uses_strategy_and_reference(self):
        candidates = build_weekly_candidates(
            [strategy()],
            [reference()],
            week_start="2026-07-06",
            limit=10,
        )
        self.assertEqual(len(candidates), 2)
        first = candidates[0]
        self.assertEqual(first["计划发布账号"], "FUNLAB IG")
        self.assertEqual(first["参考对象"], "Competitor couch gaming scene")
        self.assertEqual(first["SEO主关键词"], "hidden glow controller")
        self.assertIn("#FUNLAB", first["Hashtag词组池"])
        self.assertEqual(first["日确认状态"], "待日审")

    def test_build_weekly_candidates_skips_missing_product_pool(self):
        candidates = build_weekly_candidates(
            [strategy(**{"产品池": ""})],
            [reference()],
            week_start="2026-07-06",
            limit=10,
        )
        self.assertEqual(candidates, [])

    def test_build_weekly_candidates_skips_hero_product_pool(self):
        candidates = build_weekly_candidates(
            [strategy(**{"产品池": "FUNLAB hero product"})],
            [reference()],
            week_start="2026-07-06",
            limit=10,
        )
        self.assertEqual(candidates, [])

    def test_build_weekly_candidates_operator_strategy_overrides_default_strategy(self):
        default_strategy = strategy(
            **{
                "record_id": "rec_default",
                "提交状态": "默认锁定",
                "策略来源": "default",
                "周次": "2026-07-06",
                "产品池": "FUNLAB hero product",
            }
        )
        operator_strategy = strategy(
            **{
                "record_id": "rec_operator",
                "提交状态": "运营已提交",
                "策略来源": "operator",
                "周次": "2026-07-06",
                "产品池": "FUNLAB FF01A-04 Controller",
            }
        )
        candidates = build_weekly_candidates(
            [default_strategy, operator_strategy],
            [reference()],
            week_start="2026-07-06",
            limit=10,
        )
        self.assertEqual(len(candidates), 2)
        self.assertTrue(all(candidate["产品名"] == "FUNLAB FF01A-04 Controller" for candidate in candidates))

    def test_content_calendar_fields_from_candidate(self):
        candidate = build_weekly_candidates([strategy()], [reference()], week_start="2026-07-06", limit=1)[0]
        candidate["record_id"] = "rec_weekly"
        fields = content_calendar_fields_from_candidate(candidate)
        self.assertEqual(fields["状态"], "选题中")
        self.assertEqual(fields["AI生成状态"], "待生成")
        self.assertEqual(fields["选题来源"], "周候选池")
        self.assertEqual(fields["周候选record_id"], "rec_weekly")
        self.assertIn("SEO主关键词", fields)

    def test_content_calendar_maps_legacy_ugc_pillar(self):
        fields = content_calendar_fields_from_candidate(
            {
                "record_id": "rec_weekly",
                "fields": {
                    "候选标题": "legacy UGC candidate",
                    "产品名": "FUNLAB hero product",
                    "品牌": "FUNLAB",
                    "平台": ["Facebook"],
                    "发布位置": "FB Page",
                    "计划日期": "1784073600000",
                    "内容支柱": ["UGC"],
                    "目标信号": "Comments",
                    "实验变量": ["CTA"],
                },
            }
        )

        self.assertEqual(fields["内容支柱"], "UGC/KOL社证")

    def test_plan_weekly_endpoint_dry_run(self):
        client = TestClient(app)
        resp = client.post(
            "/plan/weekly",
            json={
                "strategies": [strategy()],
                "references": [reference()],
                "week_start": "2026-07-06",
                "write_back": False,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["status"], "weekly-plan-dry-run")
        self.assertEqual(body["generated"], 2)

    def test_plan_weekly_base_read_failure_falls_back_to_defaults(self):
        class FailingPlanClient:
            async def list_records(self, table_id, page_size=200):
                raise RuntimeError(f"base read failed: {table_id}")

            async def append_run_log(self, **kwargs):
                return {}

        app.dependency_overrides[main_module.get_settings] = lambda: Settings(
            feishu_app_id="cli_test",
            feishu_app_secret="secret",
            feishu_base_token="base",
            plan_writeback_enabled=False,
        )
        try:
            with patch.object(main_module, "_feishu", return_value=FailingPlanClient()):
                client = TestClient(app)
                resp = client.post(
                    "/plan/weekly",
                    json={"week_start": "2026-07-06", "write_back": False, "limit": 4},
                )
        finally:
            app.dependency_overrides.clear()

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["status"], "weekly-plan-dry-run")
        self.assertEqual(body["generated"], 0)
        self.assertEqual(body["candidates"], [])

    def test_plan_writeback_is_gated(self):
        app.dependency_overrides[main_module.get_settings] = lambda: Settings(plan_writeback_enabled=False)
        client = TestClient(app)
        resp = client.post(
            "/plan/weekly",
            json={
                "strategies": [strategy()],
                "references": [reference()],
                "week_start": "2026-07-06",
                "write_back": True,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["blocking"][0]["code"], "PLAN_WRITEBACK_DISABLED")

    def test_daily_confirm_and_reselect_dry_run(self):
        candidate = build_weekly_candidates([strategy()], [reference()], week_start="2026-07-06", limit=1)[0]
        candidate["record_id"] = "rec_weekly"
        candidate["样例帖子1图片Key"] = "img_v3_daily_reference_1"
        client = TestClient(app)
        confirm = client.post(
            "/plan/daily-confirm",
            json={"candidates": [{"record_id": "rec_weekly", "fields": candidate}], "target_date": "2026-07-06"},
        )
        self.assertEqual(confirm.status_code, 200)
        body = confirm.json()
        self.assertEqual(body["selected"], 1)
        self.assertEqual(body["cards"][0]["actions"][0]["action"], "confirm_generate")
        self.assertEqual(len(body["feishu_cards"]), 1)
        feishu_card = body["feishu_cards"][0]
        self.assertEqual(feishu_card["elements"][-1]["tag"], "action")
        card_text = str(feishu_card)
        self.assertIn("这张卡用来做什么", card_text)
        self.assertIn("fbig_daily_confirm", card_text)
        self.assertIn("confirm_generate", card_text)
        self.assertIn("img_v3_daily_reference_1", card_text)

        reselect = client.post(
            "/plan/reselect",
            json={
                "candidate_record_id": "rec_weekly",
                "candidate": {"record_id": "rec_weekly", "fields": candidate},
                "references": [reference(**{"参考标题": "Second reference", "账号/帖子URL": "https://example.com/ref-2"})],
                "action": "reselect_reference",
            },
        )
        self.assertEqual(reselect.status_code, 200)
        self.assertEqual(reselect.json()["fields"]["日确认状态"], "重推参考")

    def test_daily_confirm_base_read_failure_returns_empty_cards(self):
        class FailingPlanClient:
            async def list_records(self, table_id, page_size=200):
                raise RuntimeError(f"base read failed: {table_id}")

            async def append_run_log(self, **kwargs):
                return {}

        app.dependency_overrides[main_module.get_settings] = lambda: Settings(
            feishu_app_id="cli_test",
            feishu_app_secret="secret",
            feishu_base_token="base",
            plan_writeback_enabled=False,
        )
        try:
            with patch.object(main_module, "_feishu", return_value=FailingPlanClient()):
                client = TestClient(app)
                resp = client.post(
                    "/plan/daily-confirm",
                    json={"target_date": "2026-07-06", "write_back": False},
                )
        finally:
            app.dependency_overrides.clear()

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["status"], "daily-confirm-dry-run")
        self.assertEqual(body["selected"], 0)
        self.assertEqual(body["cards"], [])
        self.assertEqual(body["feishu_cards"], [])

    def test_confirm_generate_returns_content_fields_without_write(self):
        candidate = build_weekly_candidates([strategy()], [reference()], week_start="2026-07-06", limit=1)[0]
        candidate["record_id"] = "rec_weekly"
        client = TestClient(app)
        resp = client.post(
            "/plan/reselect",
            json={
                "candidate_record_id": "rec_weekly",
                "candidate": {"record_id": "rec_weekly", "fields": candidate},
                "action": "confirm_generate",
                "write_back": False,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "plan-action-dry-run")
        self.assertEqual(body["content_fields"]["状态"], "选题中")
        self.assertEqual(body["fields"]["日确认状态"], "已确认")

    def test_confirm_generate_writeback_feishu_error_is_readable(self):
        class FailingPlanClient:
            async def create_record(self, table_id, fields):
                raise main_module.FeishuError("Feishu API error: HTTP 400, code=1254015, msg=field value type mismatch")

        app.dependency_overrides[main_module.get_settings] = lambda: Settings(
            feishu_app_id="cli_test",
            feishu_app_secret="secret",
            feishu_base_token="base",
            plan_writeback_enabled=True,
        )
        try:
            with (
                patch.object(main_module, "_feishu", return_value=FailingPlanClient()),
                patch.object(main_module, "_write_log", new_callable=AsyncMock),
            ):
                client = TestClient(app)
                resp = client.post(
                    "/plan/reselect",
                    json={
                        "candidate_record_id": "rec_weekly",
                        "candidate": {
                            "record_id": "rec_weekly",
                            "fields": {
                                "候选标题": "legacy UGC candidate",
                                "产品名": "FUNLAB hero product",
                                "品牌": "FUNLAB",
                                "平台": ["Facebook"],
                                "发布位置": "FB Page",
                                "计划日期": "1784073600000",
                                "内容支柱": ["UGC"],
                                "目标信号": "Comments",
                                "实验变量": ["CTA"],
                            },
                        },
                        "action": "confirm_generate",
                        "write_back": True,
                    },
                )
        finally:
            app.dependency_overrides.clear()

        self.assertEqual(resp.status_code, 502)
        body = resp.json()
        self.assertEqual(body["detail"]["code"], "FEISHU_WRITEBACK_FAILED")
        self.assertIn("field value type mismatch", body["detail"]["message"])

    def test_confirm_generate_writeback_filters_weekly_pool_fields(self):
        class RecordingPlanClient:
            def __init__(self):
                self.weekly_update_fields = None

            async def create_record(self, table_id, fields):
                return {"record": {"record_id": "rec_content"}}

            async def update_record(self, table_id, record_id, fields):
                self.weekly_update_fields = fields
                return {"record": {"record_id": record_id, "fields": fields}}

        plan_client = RecordingPlanClient()
        app.dependency_overrides[main_module.get_settings] = lambda: Settings(
            feishu_app_id="cli_test",
            feishu_app_secret="secret",
            feishu_base_token="base",
            plan_writeback_enabled=True,
        )
        try:
            with (
                patch.object(main_module, "_feishu", return_value=plan_client),
                patch.object(main_module, "_write_log", new_callable=AsyncMock),
            ):
                client = TestClient(app)
                resp = client.post(
                    "/plan/reselect",
                    json={
                        "candidate_record_id": "rec_weekly",
                        "candidate": {
                            "record_id": "rec_weekly",
                            "fields": {
                                "候选标题": "weekly candidate",
                                "产品名": "FUNLAB hero product",
                                "品牌": "FUNLAB",
                                "平台": "Facebook",
                                "发布位置": "FB Page",
                                "计划日期": "1784073600000",
                                "内容支柱": "UGC",
                                "目标信号": "Comments",
                                "实验变量": "CTA",
                            },
                        },
                        "action": "confirm_generate",
                        "write_back": True,
                    },
                )
        finally:
            app.dependency_overrides.clear()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(plan_client.weekly_update_fields["内容日历record_id"], "rec_content")
        self.assertEqual(plan_client.weekly_update_fields["日确认状态"], "已生成内容日历")
        self.assertNotIn("日确认动作", plan_client.weekly_update_fields)
        self.assertNotIn("日确认时间", plan_client.weekly_update_fields)

    def test_resolve_product_pool_matches_sku_and_brand_model(self):
        by_sku = resolve_product_pool("FLPR008B", [product()], "FUNLAB")
        self.assertEqual(by_sku["matched"][0]["ERP SKU"], "FLPR008B")
        by_model = resolve_product_pool("FF01A-04", [product()], "FUNLAB")
        self.assertEqual(by_model["matched"][0]["品牌型号"], "FF01A-04")
        missing = resolve_product_pool("NOT-A-SKU", [product()], "FUNLAB")
        self.assertEqual(missing["unmatched"], ["NOT-A-SKU"])

    def test_weekly_input_card_and_action_dry_run(self):
        client = TestClient(app)
        card_resp = client.post(
            "/plan/weekly-input-card",
            json={"accounts": [account()], "product_index": [product()], "week_start": "2026-07-06"},
        )
        self.assertEqual(card_resp.status_code, 200)
        card_body = card_resp.json()
        card = card_body["card"]
        self.assertEqual(len(card["accounts"]), 4)
        funlab_ig = [item for item in card["accounts"] if item["account_key"] == "FUNLAB_IG"][0]
        self.assertEqual(funlab_ig["product_options"][0]["erp_sku"], "FLPR008B")
        feishu_card = card_body["feishu_card"]
        self.assertEqual(feishu_card["elements"][-1]["tag"], "form")
        weekly_actions = [item for item in feishu_card["elements"][-1]["elements"] if item.get("tag") == "button"]
        self.assertEqual(weekly_actions[0]["value"]["action"], "fbig_weekly_input")
        self.assertEqual(weekly_actions[0]["value"]["week_start"], "2026-07-06")

        action_resp = client.post(
            "/plan/weekly-input-action",
            json={
                "accounts": [account()],
                "product_index": [product()],
                "submissions": [{"account_key": "FUNLAB_IG", "本周主推产品池": "FLPR008B", "本周业务重点": "主推发光手柄"}],
                "week_start": "2026-07-06",
            },
        )
        self.assertEqual(action_resp.status_code, 200)
        body = action_resp.json()
        self.assertEqual(body["status"], "weekly-input-dry-run")
        funlab_strategy = [item for item in body["strategies"] if item["账号名称"] == "FUNLAB IG"][0]
        self.assertEqual(funlab_strategy["提交状态"], "运营已提交")
        self.assertIn("FUNLAB FF01A-04", funlab_strategy["产品池"])
        self.assertIsInstance(funlab_strategy["每周候选数"], int)

    def test_reference_discovery_candidates_are_pending(self):
        client = TestClient(app)
        resp = client.post(
            "/discovery/reference/weekly",
            json={"strategies": [strategy()], "references": [reference()], "week_start": "2026-07-06"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "reference-discovery-dry-run")
        self.assertGreaterEqual(len(body["candidates"]), 1)
        self.assertEqual(body["candidates"][0]["状态"], "待确认")
        self.assertEqual(body["candidates"][0]["审核状态"], "待确认")

    def test_kol_sample_link_gate_rejects_profiles_youtube_and_videos(self):
        self.assertFalse(discovery_module.is_visual_reference_sample_url("https://www.instagram.com/wulffden/"))
        self.assertFalse(discovery_module.is_visual_reference_sample_url("https://www.youtube.com/@WulffDen"))
        self.assertFalse(discovery_module.is_visual_reference_sample_url("https://www.instagram.com/reel/ABC123/"))
        self.assertTrue(discovery_module.is_visual_reference_sample_url("https://www.instagram.com/p/ABC123/"))
        self.assertTrue(discovery_module.is_visual_reference_sample_url("https://www.facebook.com/example/photos/a.1/2/"))

    def test_kol_visual_post_gate_rejects_people_first_images(self):
        people_first = {
            "brand": "FUNLAB",
            "post_url": "https://www.instagram.com/p/CxPeopleFirst01/",
            "thumbnail_url": "https://cdn.example.com/people-first.jpg",
            "visual_tags": ["controller", "desk setup", "image post", "博主本人 自拍 人物主体"],
            "borrow": "博主出镜讲手柄，画面主体是人脸和上半身。",
        }
        product_first = {
            "brand": "FUNLAB",
            "post_url": "https://www.instagram.com/p/CxProductFirst01/",
            "thumbnail_url": "https://cdn.example.com/product-first.jpg",
            "visual_tags": ["controller", "desk setup", "product slot", "product dominant", "no human face"],
            "borrow": "桌面电竞场景，控制器主体居中，无人物，适合替换为 FUNLAB 产品。",
        }
        self.assertTrue(discovery_module.is_people_first_visual_post(people_first))
        self.assertFalse(discovery_module.is_people_first_visual_post(product_first))
        self.assertLess(discovery_module.visual_post_fit_score(people_first), 70)
        self.assertGreaterEqual(discovery_module.visual_post_fit_score(product_first), 70)

        client = TestClient(app)
        resp = client.post(
            "/discovery/kol/visual-posts",
            json={
                "strategies": [strategy()],
                "week_start": "2026-07-06",
                "min_score": 70,
                "posts": [people_first, product_first],
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body["candidates"]), 1)
        self.assertEqual(body["candidates"][0]["样例帖子1链接"], "https://www.instagram.com/p/CxProductFirst01/")
        self.assertEqual(body["rejected"][0]["reason"], "PEOPLE_FIRST_IMAGE")

    def test_kol_weekly_and_action_dry_run(self):
        visual_kol_seeds = {
            "FUNLAB": [
                {
                    "handle": "Visual Setup Creator",
                    "url": "https://www.instagram.com/visualsetup/",
                    "platform": "Instagram",
                    "followers": "gaming setup creator",
                    "country": "US",
                    "language": "EN",
                    "style": "desk setup still photo, controller-in-hand close-up, clean product slot",
                    "visual_reason": "样例图是 IG 图片帖，画面有桌面、手部、控制器主体位置，适合替换为 FUNLAB 产品。",
                    "sample_1": "https://www.instagram.com/p/ABC123/",
                    "sample_2": "https://www.facebook.com/example/photos/a.1/2/",
                    "sample_image_1": "https://cdn.example.com/ig-abc123.jpg",
                    "sample_image_2": "https://cdn.example.com/fb-2.jpg",
                    "score": 88,
                    "risk": "不复制原图人物和账号文案。",
                },
                {
                    "handle": "Second Visual Creator",
                    "url": "https://www.instagram.com/secondvisual/",
                    "platform": "Instagram",
                    "followers": "gaming photo creator",
                    "country": "US",
                    "language": "EN",
                    "style": "low-light TV background, hands holding controller, product hero slot",
                    "visual_reason": "样例图是帖子级图片链接，适合做手柄生活方式图参考。",
                    "sample_1": "https://www.instagram.com/p/DEF456/",
                    "sample_2": "",
                    "sample_image_1": "https://cdn.example.com/ig-def456.jpg",
                    "score": 82,
                    "risk": "不复用屏幕 UI。",
                },
            ]
        }
        client = TestClient(app)
        with patch.object(discovery_module, "KOL_SEEDS", visual_kol_seeds):
            weekly = client.post(
                "/discovery/kol/weekly",
                json={
                    "strategies": [strategy()],
                    "existing_candidates": [{"fields": {"账号链接": "https://example.com/used"}}],
                    "week_start": "2026-07-06",
                    "per_brand": 5,
                },
            )
        self.assertEqual(weekly.status_code, 200)
        body = weekly.json()
        self.assertEqual(body["status"], "kol-discovery-dry-run")
        self.assertEqual(len(body["cards"]), 1)
        self.assertEqual(len(body["feishu_cards"]), 1)
        sample_links = " ".join([body["candidates"][0]["样例帖子1链接"], body["candidates"][0]["样例帖子2链接"]])
        self.assertIn("instagram.com/p/", sample_links)
        self.assertNotIn("youtube.com", sample_links)
        self.assertNotIn("instagram.com/visualsetup/", sample_links)
        action_buttons = [
            button
            for element in body["feishu_cards"][0]["elements"]
            if element.get("tag") == "action"
            for button in element.get("actions", [])
        ]
        self.assertEqual(action_buttons[0]["value"]["action"], "fbig_kol_action")
        self.assertIn(action_buttons[0]["value"]["kol_action"], {"approve", "reject_replace", "hold", "block_similar"})
        candidate = {"fields": body["candidates"][0]}

        with patch.object(discovery_module, "KOL_SEEDS", visual_kol_seeds):
            approve = client.post(
                "/discovery/kol/action",
                json={
                    "candidate": candidate,
                    "strategies": [strategy()],
                    "existing_candidates": [{"fields": {"账号链接": "https://example.com/used"}}],
                    "action": "approve",
                    "week_start": "2026-07-06",
                },
            )
        self.assertEqual(approve.status_code, 200)
        self.assertEqual(approve.json()["reference_fields"]["参考类型"], "博主图片帖")
        self.assertEqual(approve.json()["reference_fields"]["账号/帖子URL"], "https://www.instagram.com/p/ABC123/")
        self.assertEqual(approve.json()["reference_fields"]["图片帖合格性"], "合格")
        self.assertEqual(approve.json()["reference_fields"]["状态"], "可用")

        with patch.object(discovery_module, "KOL_SEEDS", visual_kol_seeds):
            reject = client.post(
                "/discovery/kol/action",
                json={
                    "candidate": candidate,
                    "strategies": [strategy()],
                    "existing_candidates": [{"fields": {"账号链接": "https://example.com/used"}}],
                    "action": "reject_replace",
                    "replacement_count": 1,
                    "week_start": "2026-07-06",
                },
            )
        self.assertEqual(reject.status_code, 200)
        self.assertEqual(reject.json()["fields"]["审核状态"], "不合适")
        self.assertEqual(len(reject.json()["replacements"]), 1)

    def test_kol_visual_posts_scores_and_builds_image_preview_card(self):
        client = TestClient(app)
        resp = client.post(
            "/discovery/kol/visual-posts",
            json={
                "strategies": [strategy()],
                "week_start": "2026-07-06",
                "min_score": 70,
                "posts": [
                    {
                        "brand": "FUNLAB",
                        "account_name": "Desk Setup Creator",
                        "account_url": "https://www.instagram.com/desksetupcreator/",
                        "post_url": "https://www.instagram.com/p/CxDeskSetup01/",
                        "thumbnail_url": "https://cdn.example.com/visual123.jpg",
                        "thumbnail_image_key": "img_v3_visual_123",
                        "followers": "45K",
                        "country": "US",
                        "language": "EN",
                        "visual_tags": ["controller", "hands", "desk setup", "product slot", "image post"],
                        "borrow": "桌面电竞场景、双手握持、控制器主体占画面中心。",
                        "avoid": "不复制博主手部、屏幕内容和原图文案。",
                    },
                    {
                        "brand": "FUNLAB",
                        "post_url": "https://www.youtube.com/@badsource",
                        "thumbnail_url": "https://cdn.example.com/youtube.jpg",
                    },
                    {
                        "brand": "FUNLAB",
                        "post_url": "https://www.instagram.com/p/CxNoPic01/",
                    },
                ],
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "kol-visual-posts-dry-run")
        self.assertEqual(len(body["candidates"]), 1)
        self.assertEqual(body["candidates"][0]["样例帖子1图片"], "https://cdn.example.com/visual123.jpg")
        self.assertEqual(body["candidates"][0]["样例帖子1图片Key"], "img_v3_visual_123")
        self.assertEqual(len(body["rejected"]), 2)
        self.assertEqual({item["reason"] for item in body["rejected"]}, {"NOT_IG_FB_IMAGE_POST", "MISSING_REFERENCE_IMAGE"})
        card_text = str(body["feishu_cards"][0])
        self.assertIn("筛选可借鉴的 IG/FB 静态图片帖", card_text)
        self.assertIn("tag': 'img'", card_text)
        self.assertIn("img_v3_visual_123", card_text)

    def test_kol_weekly_accepts_visual_posts_for_resend_card(self):
        client = TestClient(app)
        resp = client.post(
            "/discovery/kol/weekly",
            json={
                "strategies": [strategy()],
                "week_start": "2026-07-06",
                "min_visual_score": 70,
                "visual_posts": [
                    {
                        "brand": "FUNLAB",
                        "account_name": "Controller Setup Lab",
                        "account_url": "https://www.instagram.com/controllersetuplab/",
                        "post_url": "https://www.instagram.com/p/CxWeeklyScene01/",
                        "thumbnail_url": "https://cdn.example.com/weekly-visual-1.jpg",
                        "thumbnail_image_key": "img_v3_weekly_visual_1",
                        "followers": "31K",
                        "country": "US",
                        "language": "EN",
                        "visual_tags": ["controller", "hands", "desk setup", "product slot", "image post"],
                        "borrow": "TV 背景、双手握持、桌面游戏场景。",
                        "avoid": "不复制屏幕游戏内容、博主手部特征和原始文案。",
                    },
                    {
                        "brand": "FUNLAB",
                        "post_url": "https://www.youtube.com/@not-a-visual-post",
                        "thumbnail_url": "https://cdn.example.com/not-a-visual-post.jpg",
                    },
                    {
                        "brand": "FUNLAB",
                        "post_url": "https://www.instagram.com/p/WEEKLYVISUAL1/",
                        "thumbnail_url": "https://cdn.example.com/placeholder.jpg",
                    },
                ],
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "kol-discovery-dry-run")
        self.assertEqual(len(body["candidates"]), 1)
        self.assertEqual(body["candidates"][0]["样例帖子1图片"], "https://cdn.example.com/weekly-visual-1.jpg")
        self.assertEqual(body["candidates"][0]["样例帖子1图片Key"], "img_v3_weekly_visual_1")
        self.assertEqual(len(body["rejected"]), 2)
        self.assertEqual({item["reason"] for item in body["rejected"]}, {"NOT_IG_FB_IMAGE_POST", "PLACEHOLDER_POST_URL"})
        card_text = str(body["feishu_cards"][0])
        self.assertIn("这张卡用来做什么", card_text)
        self.assertIn("tag': 'img'", card_text)
        self.assertIn("img_v3_weekly_visual_1", card_text)

    def test_kol_visual_posts_can_prepare_feishu_image_keys(self):
        app.dependency_overrides[main_module.get_settings] = lambda: Settings(
            service_token="",
            feishu_app_id="app",
            feishu_app_secret="secret",
            feishu_base_token="base",
        )
        client = TestClient(app)
        with patch.object(
            main_module.FeishuClient,
            "upload_message_image_from_url",
            new=AsyncMock(return_value="img_v3_generated_preview_key"),
        ) as upload_mock:
            resp = client.post(
                "/discovery/kol/visual-posts",
                json={
                    "strategies": [strategy()],
                    "week_start": "2026-07-06",
                    "prepare_image_keys": True,
                    "posts": [
                        {
                            "brand": "FUNLAB",
                            "account_name": "Desk Setup Creator",
                            "account_url": "https://www.instagram.com/desksetupcreator/",
                            "post_url": "https://www.instagram.com/p/CxDeskSetup02/",
                            "thumbnail_url": "https://cdn.example.com/desk-setup-02.jpg",
                            "followers": "45K",
                            "country": "US",
                            "language": "EN",
                            "visual_tags": ["controller", "hands", "desk setup", "product slot", "image post"],
                            "borrow": "桌面电竞场景、双手握持、控制器主体占画面中心。",
                            "avoid": "不复制博主手部、屏幕内容和原图文案。",
                        }
                    ],
                },
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["image_key_errors"], [])
        self.assertEqual(body["candidates"][0]["样例帖子1图片Key"], "img_v3_generated_preview_key")
        card_text = str(body["feishu_cards"][0])
        self.assertIn("tag': 'img'", card_text)
        self.assertIn("img_v3_generated_preview_key", card_text)
        upload_mock.assert_awaited_once()

    def test_kol_visual_posts_writeback_strips_card_only_image_keys(self):
        app.dependency_overrides[main_module.get_settings] = lambda: Settings(
            service_token="",
            feishu_app_id="app",
            feishu_app_secret="secret",
            feishu_base_token="base",
            plan_writeback_enabled=True,
        )
        client = TestClient(app)
        create_mock = AsyncMock(
            return_value={
                "record": {
                    "record_id": "rec_kol_visual",
                    "fields": {
                        "审核状态": "待确认",
                        "状态": "待确认",
                    },
                }
            }
        )
        try:
            with (
                patch.object(
                    main_module.FeishuClient,
                    "upload_message_image_from_url",
                    new=AsyncMock(return_value="img_v3_generated_preview_key"),
                ),
                patch.object(main_module.FeishuClient, "create_record", new=create_mock),
            ):
                resp = client.post(
                    "/discovery/kol/visual-posts",
                    json={
                        "strategies": [strategy()],
                        "week_start": "2026-07-06",
                        "write_back": True,
                        "prepare_image_keys": True,
                        "posts": [
                            {
                                "brand": "FUNLAB",
                                "account_name": "Desk Setup Creator",
                                "account_url": "https://www.instagram.com/desksetupcreator/",
                                "post_url": "https://www.instagram.com/p/CxDeskSetup02/",
                                "thumbnail_url": "https://cdn.example.com/desk-setup-02.jpg",
                                "followers": "45K",
                                "country": "US",
                                "language": "EN",
                                "visual_tags": ["controller", "hands", "desk setup", "product slot", "image post"],
                                "borrow": "桌面电竞场景、双手握持、控制器主体占画面中心。",
                                "avoid": "不复制博主手部、屏幕内容和原图文案。",
                            }
                        ],
                    },
                )
        finally:
            app.dependency_overrides.clear()
        self.assertEqual(resp.status_code, 200)
        written_fields = next(arg for arg in create_mock.await_args.args if isinstance(arg, dict))
        self.assertNotIn("样例帖子1图片Key", written_fields)
        self.assertNotIn("样例帖子2图片Key", written_fields)
        body = resp.json()
        self.assertEqual(body["created"][0]["record"]["record_id"], "rec_kol_visual")
        card_text = str(body["feishu_cards"][0])
        self.assertIn("rec_kol_visual", card_text)
        self.assertIn("tag': 'img'", card_text)
        self.assertIn("img_v3_generated_preview_key", card_text)


if __name__ == "__main__":
    unittest.main()
