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
    service_token: str = os.getenv("SOCIAL_PUBLISH_API_TOKEN", "")
    commit_enabled: bool = env_bool("SOCIAL_PUBLISH_COMMIT_ENABLED", False)
    generation_writeback_enabled: bool = env_bool("SOCIAL_GENERATION_WRITEBACK_ENABLED", False)
    image_task_write_enabled: bool = env_bool("SOCIAL_IMAGE_TASK_WRITE_ENABLED", False)
    image_result_writeback_enabled: bool = env_bool("SOCIAL_IMAGE_RESULT_WRITEBACK_ENABLED", False)
    asset_prepare_enabled: bool = env_bool("SOCIAL_ASSET_PREPARE_ENABLED", False)
    dry_run_write_logs: bool = env_bool("SOCIAL_PUBLISH_DRY_RUN_WRITE_LOGS", True)

    feishu_app_id: str = os.getenv("FEISHU_APP_ID", "")
    feishu_app_secret: str = os.getenv("FEISHU_APP_SECRET", "")
    feishu_base_token: str = os.getenv("FEISHU_BASE_TOKEN", "JXw5bUmRoaaCPqsbc6HctWfknhe")
    content_table_id: str = os.getenv("FEISHU_CONTENT_TABLE_ID", "tblhVnKqqhTXvO3Y")
    account_table_id: str = os.getenv("FEISHU_ACCOUNT_TABLE_ID", "tbliJB7jJmXVTaXf")
    log_table_id: str = os.getenv("FEISHU_LOG_TABLE_ID", "tblgpuSW1cFKg2t7")
    metrics_table_id: str = os.getenv("FEISHU_METRICS_TABLE_ID", "tbl6LPXQlpXg9KXG")
    image_task_base_token: str = os.getenv("FEISHU_IMAGE_TASK_BASE_TOKEN", "Y0mdb6727arI58sLBsIcI7i3ncc")
    image_task_table_id: str = os.getenv("FEISHU_IMAGE_TASK_TABLE_ID", "tblXrErgSSj2I5uI")

    generation_ai_provider: str = os.getenv("GENERATION_AI_PROVIDER", "template")
    generation_ai_base_url: str = os.getenv("GENERATION_AI_BASE_URL", "https://api.deepseek.com")
    generation_ai_api_key: str = os.getenv("GENERATION_AI_API_KEY", "")
    generation_ai_model: str = os.getenv("GENERATION_AI_MODEL", "deepseek-chat")
    generation_ai_timeout_seconds: float = float(os.getenv("GENERATION_AI_TIMEOUT_SECONDS", "45"))

    def feishu_enabled(self) -> bool:
        return bool(self.feishu_app_id and self.feishu_app_secret and self.feishu_base_token)

    def meta_enabled(self) -> bool:
        return bool(self.meta_access_token)

    def generation_ai_enabled(self) -> bool:
        return self.generation_ai_provider != "template" and bool(self.generation_ai_api_key)

    def image_task_enabled(self) -> bool:
        return bool(self.feishu_app_id and self.feishu_app_secret and self.image_task_base_token)


def get_settings() -> Settings:
    return Settings()
