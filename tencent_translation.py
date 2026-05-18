from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from dotenv import dotenv_values
except ImportError:
    dotenv_values = None


@dataclass(frozen=True)
class TencentTranslationConfig:
    secret_id: str
    secret_key: str
    region: str = "ap-guangzhou"
    source: str = "en"
    target: str = "zh"
    cache_path: Path = Path("data/translation_cache.json")
    retry_count: int = 2
    sleep_seconds: float = 0.3
    request_timeout: int = 60


TRANSLATION_CACHE_VERSION = 1


def text_value(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def load_env_values(env_path: Path) -> dict[str, str]:
    if not env_path.exists():
        return {}

    if dotenv_values is not None:
        return {
            key: text_value(value)
            for key, value in dotenv_values(env_path).items()
            if key
        }

    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def env_value(values: dict[str, str], key: str, default: str = "") -> str:
    return text_value(values.get(key) or os.environ.get(key) or default).strip()


def load_tencent_translation_config(
    env_path: Path = Path(".env"),
    cache_path: Path = Path("data/translation_cache.json"),
) -> TencentTranslationConfig | None:
    values = load_env_values(env_path)
    secret_id = env_value(values, "TENCENTCLOUD_SECRET_ID")
    secret_key = env_value(values, "TENCENTCLOUD_SECRET_KEY")
    if not secret_id or not secret_key:
        return None

    timeout_text = env_value(values, "TENCENT_TRANSLATE_TIMEOUT", "60")
    try:
        request_timeout = int(timeout_text)
    except ValueError:
        request_timeout = 60

    return TencentTranslationConfig(
        secret_id=secret_id,
        secret_key=secret_key,
        region=env_value(values, "TENCENTCLOUD_REGION", "ap-guangzhou") or "ap-guangzhou",
        source=env_value(values, "TENCENT_TRANSLATE_SOURCE", "en") or "en",
        target=env_value(values, "TENCENT_TRANSLATE_TARGET", "zh") or "zh",
        cache_path=cache_path,
        request_timeout=request_timeout,
    )


def translation_cache_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def empty_translation_cache() -> dict[str, dict[str, dict[str, str]]]:
    return {"titles": {}, "abstracts": {}}


def load_translation_cache(path: Path) -> dict[str, dict[str, dict[str, str]]]:
    if not path.exists():
        return empty_translation_cache()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"[WARN] Translation cache ignored: {error}")
        return empty_translation_cache()

    cache = empty_translation_cache()
    if not isinstance(raw, dict):
        return cache

    for bucket_name in ("titles", "abstracts"):
        raw_bucket = raw.get(bucket_name, {})
        if not isinstance(raw_bucket, dict):
            continue
        for key, entry in raw_bucket.items():
            if not isinstance(key, str) or not isinstance(entry, dict):
                continue
            translation = text_value(entry.get("translation", "")).strip()
            source = text_value(entry.get("source", "")).strip()
            if translation:
                cache[bucket_name][key] = {
                    "source": source,
                    "translation": translation,
                }

    return cache


def save_translation_cache(path: Path, cache: dict[str, dict[str, dict[str, str]]]) -> None:
    payload = {
        "version": TRANSLATION_CACHE_VERSION,
        "titles": cache.get("titles", {}),
        "abstracts": cache.get("abstracts", {}),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def save_translation_cache_safely(path: Path, cache: dict[str, dict[str, dict[str, str]]]) -> bool:
    try:
        save_translation_cache(path, cache)
    except OSError as error:
        print(f"[WARN] Translation cache could not be saved: {error}")
        return False
    return True


def read_cached_translation(
    cache: dict[str, dict[str, dict[str, str]]],
    bucket_name: str,
    source_text: str,
) -> str:
    source_text = source_text.strip()
    if not source_text:
        return ""

    entry = cache.get(bucket_name, {}).get(translation_cache_key(source_text), {})
    if not isinstance(entry, dict):
        return ""
    return text_value(entry.get("translation", "")).strip()


def write_cached_translation(
    cache: dict[str, dict[str, dict[str, str]]],
    bucket_name: str,
    source_text: str,
    translation: str,
) -> bool:
    source_text = source_text.strip()
    translation = translation.strip()
    if not source_text or not translation:
        return False

    bucket = cache.setdefault(bucket_name, {})
    key = translation_cache_key(source_text)
    existing = bucket.get(key)
    if existing and existing.get("translation") == translation:
        return False

    bucket[key] = {
        "source": source_text,
        "translation": translation,
    }
    return True


def redact_secret(text: str, config: TencentTranslationConfig) -> str:
    redacted = text
    for secret in (config.secret_id, config.secret_key):
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


def is_retryable_error(error: Exception) -> bool:
    message = f"{type(error).__name__} {error}".lower()
    markers = (
        "failedoperation.requestlimitexceeded",
        "requestlimitexceeded",
        "limitexceeded",
        "ratelimit",
        "rate limit",
        "throttl",
        "timeout",
        "timed out",
        "connection",
        "network",
        "temporarily",
    )
    return any(marker in message for marker in markers)


def build_tencent_client(config: TencentTranslationConfig) -> Any:
    try:
        from tencentcloud.common import credential
        from tencentcloud.common.profile.client_profile import ClientProfile
        from tencentcloud.common.profile.http_profile import HttpProfile
        from tencentcloud.tmt.v20180321 import tmt_client
    except ImportError as error:
        raise RuntimeError(
            "tencentcloud-sdk-python is required for Tencent Cloud translation. "
            "Run: pip install -r requirements.txt"
        ) from error

    cred = credential.Credential(config.secret_id, config.secret_key)
    http_profile = HttpProfile()
    http_profile.endpoint = "tmt.tencentcloudapi.com"
    http_profile.reqTimeout = config.request_timeout
    client_profile = ClientProfile()
    client_profile.httpProfile = http_profile
    return tmt_client.TmtClient(cred, config.region, client_profile)


def translate_text_with_tencent(client: Any, text: str, config: TencentTranslationConfig) -> str:
    text = text.strip()
    if not text:
        return ""

    from tencentcloud.tmt.v20180321 import models

    request = models.TextTranslateRequest()
    request.from_json_string(
        json.dumps(
            {
                "SourceText": text,
                "Source": config.source,
                "Target": config.target,
                "ProjectId": 0,
            },
            ensure_ascii=False,
        )
    )
    response = client.TextTranslate(request)
    data = json.loads(response.to_json_string())
    return text_value(data.get("TargetText", "")).strip()


def maybe_translate_items(
    items: list[dict[str, Any]],
    config: TencentTranslationConfig | None,
) -> list[dict[str, Any]]:
    if config is None:
        for item in items:
            item.setdefault("title_zh", "")
            item.setdefault("abstract_zh", "")
        return items

    try:
        client = build_tencent_client(config)
    except Exception as error:
        print(f"[WARN] Tencent translation disabled: {redact_secret(str(error), config)}")
        for item in items:
            item.setdefault("title_zh", "")
            item.setdefault("abstract_zh", "")
        return items

    cache = load_translation_cache(config.cache_path)
    cache_changed = False
    last_request_at = 0.0

    def translate_uncached(bucket_name: str, source_text: str) -> str:
        nonlocal last_request_at
        attempts = config.retry_count + 1
        for attempt in range(1, attempts + 1):
            elapsed = time.monotonic() - last_request_at
            if last_request_at and elapsed < config.sleep_seconds:
                time.sleep(config.sleep_seconds - elapsed)
            try:
                translated_text = translate_text_with_tencent(client, source_text, config)
                if translated_text:
                    write_cached_translation(cache, bucket_name, source_text, translated_text)
                return translated_text
            except Exception as error:
                if attempt >= attempts or not is_retryable_error(error):
                    raise
                print(
                    "[WARN] Tencent translation retry "
                    f"{attempt}/{config.retry_count}: {redact_secret(str(error), config)}"
                )
            finally:
                last_request_at = time.monotonic()
        return ""

    for index, item in enumerate(items, start=1):
        title_en = text_value(item.get("title_en", "")).strip()
        abstract_en = text_value(item.get("abstract_en", "")).strip()
        title_zh = text_value(item.get("title_zh", "")).strip()
        abstract_zh = text_value(item.get("abstract_zh", "")).strip()

        if title_zh:
            cache_changed = write_cached_translation(cache, "titles", title_en, title_zh) or cache_changed
        if abstract_zh:
            cache_changed = write_cached_translation(cache, "abstracts", abstract_en, abstract_zh) or cache_changed

        if not title_zh and title_en:
            cached_title = read_cached_translation(cache, "titles", title_en)
            if cached_title:
                item["title_zh"] = cached_title
                title_zh = cached_title

        if not abstract_zh and abstract_en:
            cached_abstract = read_cached_translation(cache, "abstracts", abstract_en)
            if cached_abstract:
                item["abstract_zh"] = cached_abstract
                abstract_zh = cached_abstract

        if not title_zh and title_en:
            try:
                print(f"[INFO] Translating title {index}/{len(items)} with Tencent Cloud")
                item["title_zh"] = translate_uncached("titles", title_en)
                cache_changed = True
            except Exception as error:
                print(f"[WARN] Tencent title translation skipped: {redact_secret(str(error), config)}")

        if not abstract_zh and abstract_en:
            try:
                print(f"[INFO] Translating abstract {index}/{len(items)} with Tencent Cloud")
                item["abstract_zh"] = translate_uncached("abstracts", abstract_en)
                cache_changed = True
            except Exception as error:
                print(f"[WARN] Tencent abstract translation skipped: {redact_secret(str(error), config)}")

        if cache_changed and save_translation_cache_safely(config.cache_path, cache):
            cache_changed = False

    if cache_changed:
        save_translation_cache_safely(config.cache_path, cache)

    return items
