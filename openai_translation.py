from __future__ import annotations

from tencent_translation import (
    TencentTranslationConfig,
    load_tencent_translation_config,
    maybe_translate_items,
)

# Backward-compatible names for older local scripts that may still import this module.
OpenAITranslationConfig = TencentTranslationConfig
load_openai_translation_config = load_tencent_translation_config
