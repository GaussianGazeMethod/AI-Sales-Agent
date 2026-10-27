"""将项目内业务skill及其文档依赖封装为Agently原生不可变修订。

只处理本地包和文件引用，不承担语义路由、会话状态或业务执行。
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from app.ai.runtime import PROJECT_ROOT

SKILLS_ROOT = PROJECT_ROOT.parent / "spec" / "skills"
_LINK = re.compile(r"(?P<prefix>\]\()(?P<target>[^)]+)(?P<suffix>\))")


def _package(source: Path, destination: Path, allowed_root: Path) -> dict[Path, bytes]:
    """安装包内收齐Markdown链接依赖并返回实际打包的原始源文件。"""
    mapped: dict[Path, Path] = {source.resolve(): Path("SKILL.md")}
    packaged_sources: dict[Path, bytes] = {}

    def copy_resource(path: Path) -> Path:
        path = path.resolve()
        if not any(path.is_relative_to(allowed_root / folder) for folder in ("spec", "docs")):
            raise ValueError("业务skill依赖超出允许的spec/docs目录")
        relative_parts = path.relative_to(allowed_root).parts
        if any(part.startswith(".") for part in relative_parts) or path.suffix.lower() in {
            ".key",
            ".pem",
            ".p12",
            ".pfx",
            ".jks",
            ".keystore",
        }:
            raise ValueError("业务skill依赖不允许包含隐藏或密钥文件")
        if not path.is_file():
            raise ValueError(f"业务skill依赖不存在：{path.name}")
        source_bytes = path.read_bytes()
        packaged_sources[path] = source_bytes
        relative = mapped.get(path)
        if relative is None:
            digest = hashlib.sha256(str(path.relative_to(allowed_root)).encode()).hexdigest()[:12]
            relative = Path("references") / f"{digest}-{path.name}"
            mapped[path] = relative
        target = destination / relative
        if target.exists():
            return relative
        target.parent.mkdir(parents=True, exist_ok=True)
        # 先占位以处理互相引用；完成前不会交给原生安装器。
        target.touch()
        if path.suffix.lower() != ".md":
            target.write_bytes(source_bytes)
            return relative

        def replace(match: re.Match[str]) -> str:
            raw = match.group("target")
            parsed = urlsplit(raw)
            if parsed.scheme or parsed.netloc or not parsed.path:
                return match.group(0)
            # 业务spec正文中的其他页面链接是导航，不是skill执行依赖。
            # 保留原资料定位，不递归打包整套需求库或访问其附件。
            if not path.is_relative_to(allowed_root / "spec" / "skills"):
                return f"]（项目资料引用：{raw}）"
            dependency = copy_resource(path.parent / unquote(parsed.path))
            # 所有引用资源在同一references目录，入口在包根目录。
            link = dependency.as_posix() if relative.name == "SKILL.md" else dependency.name
            if parsed.fragment:
                link += "#" + parsed.fragment
            return f"]({link})"

        target.write_text(
            _LINK.sub(replace, source_bytes.decode("utf-8")), encoding="utf-8"
        )
        return relative

    copy_resource(source)
    return packaged_sources


def install_business_skills(
    library: Any,
    *,
    skills_root: Path = SKILLS_ROOT,
    source_inventory: dict[str, dict[Path, bytes]] | None = None,
) -> dict[str, Any]:
    """向调用方提供的原生SkillLibrary安装版本；不创建另一套注册/状态存储。"""
    root = skills_root.resolve()
    sources = sorted(root.glob("*/SKILL.md"))
    if not sources:
        raise ValueError("未发现业务skill")
    revisions: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="market-skill-package-") as work:
        for source in sources:
            package = Path(work) / source.parent.name
            packaged_sources = _package(source, package, root.parent.parent)
            revision = library.install(package, scope="explicit", trust="trusted")
            if revision.skill_id in revisions:
                raise ValueError(f"重复业务skill：{revision.skill_id}")
            revisions[revision.skill_id] = revision
            if source_inventory is not None:
                source_inventory[revision.skill_id] = packaged_sources
    return revisions
