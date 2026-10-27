"""R1–R6语义基准的发布内容摘要（T09发布快照的最小锚）。

基准文本唯一权威是 spec/doing/13-R1-R6风险判断基准.md；内容寻址摘要供历史预警在创建时
锚定"当时生效的规则内容"，只读文件、不依赖Agently，也不提供修改入口——发布=改文件
经重启产生新sha256修订。旧行不回填不重判。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
SPEC_13 = os.environ.get(
    "RISK_RULES_SPEC_PATH", str(_PROJECT_ROOT / "spec" / "doing" / "13-R1-R6风险判断基准.md")
)


def risk_rules_digest() -> str | None:
    """当前生效基准文本的内容摘要；文件不可读时返回None（不阻断业务写入）。"""
    try:
        return hashlib.sha256(Path(SPEC_13).read_bytes()).hexdigest()[:16]
    except OSError:
        return None
