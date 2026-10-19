# -*- coding: utf-8 -*-
"""运行时模型设置接口。

- GET /api/v4/settings  读取当前生效的三个模型名
- PUT /api/v4/settings  修改并立即生效（落盘到 OUTPUT_DIR/settings.json）

只管理模型名；API key / base_url / JUDGE_PROVIDERS 仍由部署配置管理。
保存后无需重启：反向 LLM 工厂与正向配置生成都按调用读取环境变量。
"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from app import runtime_settings
from app.api.v4.schemas import V4ModelSettings, V4ModelSettingsUpdate


router = APIRouter()


def _to_response(settings: runtime_settings.ModelSettings) -> V4ModelSettings:
    return V4ModelSettings(**asdict(settings))


@router.get('/settings', response_model=V4ModelSettings)
def get_settings() -> V4ModelSettings:
    """返回当前生效值；未自定义时来自部署配置（.env）或 factory 默认值。"""
    return _to_response(runtime_settings.get_model_settings())


@router.put('/settings', response_model=V4ModelSettings)
def update_settings(payload: V4ModelSettingsUpdate) -> V4ModelSettings:
    """校验并保存三个模型名，立即应用到后续模型调用。"""
    try:
        updated = runtime_settings.update_model_settings(
            deepseek_model=payload.deepseek_model,
            minimax_model=payload.minimax_model,
            qwen_model=payload.qwen_model,
        )
    except runtime_settings.SettingsValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f'无法写入设置文件 {runtime_settings.settings_path()}: {exc}',
        )
    return _to_response(updated)
