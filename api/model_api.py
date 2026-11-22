"""可选的模型配置清单，供生成对话框和新建小说向导展示。

只返回配置完整（密钥、地址、模型名都存在）的档案；密钥不会下发。
"""

from fastapi import APIRouter

from api.account_api import CurrentAccount
from core.models import DEFAULT_PROFILE, available_profiles


router = APIRouter(prefix="/models", tags=["模型"])


@router.get("", summary="查询可用的模型配置")
def list_models(user: CurrentAccount) -> dict:
    return {"items": [profile.public() for profile in available_profiles()], "default": DEFAULT_PROFILE}
