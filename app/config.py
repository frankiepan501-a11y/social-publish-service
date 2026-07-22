from dataclasses import dataclass
import os


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    graph_version: str = os.getenv("META_GRAPH_VERSION", "v25.0")
    meta_access_token: str = os.getenv("META_ACCESS_TOKEN", "")
    meta_access_token_powkong: str = os.getenv("META_ACCESS_TOKEN_POWKONG", "")
    meta_access_token_funlab: str = os.getenv("META_ACCESS_TOKEN_FUNLAB", "")
    service_token: str = os.getenv("SOCIAL_PUBLISH_API_TOKEN", "")
    commit_enabled: bool = env_bool("SOCIAL_PUBLISH_COMMIT_ENABLED", False)
    generation_writeback_enabled: bool = env_bool("SOCIAL_GENERATION_WRITEBACK_ENABLED", False)
    image_task_write_enabled: bool = env_bool("SOCIAL_IMAGE_TASK_WRITE_ENABLED", False)
    image_result_writeback_enabled: bool = env_bool("SOCIAL_IMAGE_RESULT_WRITEBACK_ENABLED", False)
    asset_prepare_enabled: bool = env_bool("SOCIAL_ASSET_PREPARE_ENABLED", False)
    approval_writeback_enabled: bool = env_bool("SOCIAL_APPROVAL_WRITEBACK_ENABLED", False)
    plan_writeback_enabled: bool = env_bool("SOCIAL_PLAN_WRITEBACK_ENABLED", False)
    dry_run_write_logs: bool = env_bool("SOCIAL_PUBLISH_DRY_RUN_WRITE_LOGS", True)

    feishu_app_id: str = os.getenv("FEISHU_APP_ID", "")
    feishu_app_secret: str = os.getenv("FEISHU_APP_SECRET", "")
    feishu_bitable_app_id: str = os.getenv("FEISHU_BITABLE_APP_ID", os.getenv("FEISHU_APP_ID", ""))
    feishu_bitable_app_secret: str = os.getenv("FEISHU_BITABLE_APP_SECRET", os.getenv("FEISHU_APP_SECRET", ""))
    feishu_base_token: str = os.getenv("FEISHU_BASE_TOKEN", "JXw5bUmRoaaCPqsbc6HctWfknhe")
    content_table_id: str = os.getenv("FEISHU_CONTENT_TABLE_ID", "tblhVnKqqhTXvO3Y")
    account_table_id: str = os.getenv("FEISHU_ACCOUNT_TABLE_ID", "tbliJB7jJmXVTaXf")
    strategy_table_id: str = os.getenv("FEISHU_STRATEGY_TABLE_ID", "tblyavkR6xdEt9gd")
    reference_table_id: str = os.getenv("FEISHU_REFERENCE_TABLE_ID", "tblMDwMv07jbeGAE")
    weekly_pool_table_id: str = os.getenv("FEISHU_WEEKLY_POOL_TABLE_ID", "tblV8rGXyRWGsE5r")
    product_index_table_id: str = os.getenv("FEISHU_PRODUCT_INDEX_TABLE_ID", "tblfI565xItYpXhE")
    kol_candidate_table_id: str = os.getenv("FEISHU_KOL_CANDIDATE_TABLE_ID", "tblAIDN2cMSQVgGR")
    weekly_review_table_id: str = os.getenv("FEISHU_WEEKLY_REVIEW_TABLE_ID", "tblyJyHRa4Egm4ft")
    log_table_id: str = os.getenv("FEISHU_LOG_TABLE_ID", "tblgpuSW1cFKg2t7")
    metrics_table_id: str = os.getenv("FEISHU_METRICS_TABLE_ID", "tbl6LPXQlpXg9KXG")
    image_task_base_token: str = os.getenv("FEISHU_IMAGE_TASK_BASE_TOKEN", "Y0mdb6727arI58sLBsIcI7i3ncc")
    image_task_table_id: str = os.getenv("FEISHU_IMAGE_TASK_TABLE_ID", "tblXrErgSSj2I5uI")
    product_library_base_token: str = os.getenv("FEISHU_PRODUCT_LIBRARY_BASE_TOKEN", "MvtZb6OE9aJFaisO913cWSErnFe")
    product_powkong_table_id: str = os.getenv("FEISHU_PRODUCT_POWKONG_TABLE_ID", "tblBCI4QaOZAgv3r")
    product_funlab_table_id: str = os.getenv("FEISHU_PRODUCT_FUNLAB_TABLE_ID", "tblwJ3BRkIuHDuSK")
    social_crm_p0_write_enabled: bool = env_bool("SOCIAL_CRM_P0_WRITE_ENABLED", False)
    social_crm_p0_base_token: str = os.getenv("SOCIAL_CRM_P0_BASE_TOKEN", "Zai5bH4RdasnLWsCU9ecB7tEnSb")
    social_crm_p0_post_table_id: str = os.getenv("SOCIAL_CRM_P0_POST_TABLE_ID", "tblCLfsU9oTcyaRz")
    social_crm_p0_snapshot_table_id: str = os.getenv("SOCIAL_CRM_P0_SNAPSHOT_TABLE_ID", "tblknQFFK8rf5YeW")
    social_crm_meta_service_url: str = os.getenv(
        "SOCIAL_CRM_META_SERVICE_URL",
        os.getenv("SOCIAL_PUBLISH_SERVICE_URL", "https://fb-ig-social-publish.zeabur.app"),
    )
    social_crm_youtube_oauth_client_json: str = os.getenv("SOCIAL_CRM_YOUTUBE_OAUTH_CLIENT_JSON", "")
    social_crm_youtube_token_funlab_json: str = os.getenv("SOCIAL_CRM_YOUTUBE_TOKEN_FUNLAB_JSON", "")
    social_crm_youtube_token_powkong_json: str = os.getenv("SOCIAL_CRM_YOUTUBE_TOKEN_POWKONG_JSON", "")
    social_crm_x_client_funlab_json: str = os.getenv("SOCIAL_CRM_X_CLIENT_FUNLAB_JSON", "")
    social_crm_x_token_funlab_json: str = os.getenv("SOCIAL_CRM_X_TOKEN_FUNLAB_JSON", "")
    social_crm_x_client_powkong_json: str = os.getenv("SOCIAL_CRM_X_CLIENT_POWKONG_JSON", "")
    social_crm_x_token_powkong_json: str = os.getenv("SOCIAL_CRM_X_TOKEN_POWKONG_JSON", "")
    social_crm_x_token_persist_path: str = os.getenv("SOCIAL_CRM_X_TOKEN_PERSIST_PATH", "/tmp/social_crm_x_tokens.json")
    social_crm_x_zeabur_env_persist_enabled: bool = env_bool("SOCIAL_CRM_X_ZEABUR_ENV_PERSIST_ENABLED", False)
    social_crm_x_zeabur_api_key: str = os.getenv("SOCIAL_CRM_X_ZEABUR_API_KEY", "")
    social_crm_x_zeabur_service_id: str = os.getenv("SOCIAL_CRM_X_ZEABUR_SERVICE_ID", "")
    social_crm_x_zeabur_environment_id: str = os.getenv("SOCIAL_CRM_X_ZEABUR_ENVIRONMENT_ID", "")
    social_crm_x_zeabur_graphql_url: str = os.getenv("SOCIAL_CRM_X_ZEABUR_GRAPHQL_URL", "https://api.zeabur.com/graphql")
    social_crm_x_token_funlab_env_key: str = os.getenv("SOCIAL_CRM_X_TOKEN_FUNLAB_ENV_KEY", "SOCIAL_CRM_X_TOKEN_FUNLAB_JSON")
    social_crm_x_token_powkong_env_key: str = os.getenv("SOCIAL_CRM_X_TOKEN_POWKONG_ENV_KEY", "SOCIAL_CRM_X_TOKEN_POWKONG_JSON")
    social_crm_p1_publish_enabled: bool = env_bool("SOCIAL_CRM_P1_PUBLISH_ENABLED", False)

    generation_ai_provider: str = os.getenv("GENERATION_AI_PROVIDER", "template")
    generation_ai_base_url: str = os.getenv("GENERATION_AI_BASE_URL", "https://api.deepseek.com")
    generation_ai_api_key: str = os.getenv("GENERATION_AI_API_KEY", "")
    generation_ai_model: str = os.getenv("GENERATION_AI_MODEL", "deepseek-chat")
    generation_ai_timeout_seconds: float = float(os.getenv("GENERATION_AI_TIMEOUT_SECONDS", "45"))

    def feishu_enabled(self) -> bool:
        return bool(
            (self.feishu_bitable_app_id and self.feishu_bitable_app_secret or self.feishu_app_id and self.feishu_app_secret)
            and self.feishu_base_token
        )

    def meta_enabled(self) -> bool:
        return bool(self.meta_access_token or self.meta_access_token_powkong or self.meta_access_token_funlab)

    def meta_token_for_brand(self, brand: str | None) -> str:
        normalized = (brand or "").strip().lower()
        if normalized == "powkong" and self.meta_access_token_powkong:
            return self.meta_access_token_powkong
        if normalized == "funlab" and self.meta_access_token_funlab:
            return self.meta_access_token_funlab
        return self.meta_access_token

    def generation_ai_enabled(self) -> bool:
        return self.generation_ai_provider != "template" and bool(self.generation_ai_api_key)

    def image_task_enabled(self) -> bool:
        return bool(self.feishu_app_id and self.feishu_app_secret and self.image_task_base_token)

    def product_library_enabled(self) -> bool:
        return bool(self.feishu_app_id and self.feishu_app_secret and self.product_library_base_token)

    def social_crm_p0_base_enabled(self) -> bool:
        return bool(
            self.feishu_bitable_app_id
            and self.feishu_bitable_app_secret
            and self.social_crm_p0_base_token
            and self.social_crm_p0_post_table_id
            and self.social_crm_p0_snapshot_table_id
        )

    def social_crm_p0_youtube_enabled(self) -> bool:
        return bool(
            self.social_crm_youtube_oauth_client_json
            and self.social_crm_youtube_token_funlab_json
            and self.social_crm_youtube_token_powkong_json
        )

    def social_crm_p0_x_enabled(self) -> bool:
        return bool(
            self.social_crm_x_client_funlab_json
            and self.social_crm_x_token_funlab_json
            and self.social_crm_x_client_powkong_json
            and self.social_crm_x_token_powkong_json
        )

    def social_crm_p0_x_durable_persist_enabled(self) -> bool:
        return bool(
            self.social_crm_x_zeabur_env_persist_enabled
            and self.social_crm_x_zeabur_api_key
            and self.social_crm_x_zeabur_service_id
            and self.social_crm_x_zeabur_environment_id
        )

    def social_crm_p1_publish_configured(self) -> bool:
        return self.meta_enabled() and self.feishu_enabled()


def get_settings() -> Settings:
    return Settings()
