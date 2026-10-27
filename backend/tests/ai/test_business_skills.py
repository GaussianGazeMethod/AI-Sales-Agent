"""原生skill安装与资源依赖验证；不调用模型，不代表语义路由通过。"""

import pytest
from agently import Agently

from app.ai.business_skills import _package, install_business_skills


def test_native_install_preserves_five_skills_and_bounded_dependencies(tmp_path):
    # 使用独立原生库实例，不能修改应用全局库配置。
    library = type(Agently.skill_library)()
    library.configure(root=tmp_path / "library")
    first = install_business_skills(library)
    second = install_business_skills(library)
    assert set(first) == {
        "lead-intake-validation",
        "sales-project-followup",
        "project-dossier-management",
        "project-diagnosis",
        "project-requirements-report",
    }
    for name, revision in first.items():
        assert revision.revision_ref == second[name].revision_ref
        assert revision.trust == "trusted"
        paths = {resource.path for resource in revision.resources}
        assert "SKILL.md" in paths
        assert any(path.endswith("-contract.md") for path in paths)
        for resource in revision.resources:
            content = library.read_resource(revision, resource.path)
            assert content.data
    report = first["project-requirements-report"]
    assert any(r.path.endswith("-13-R1-R6风险判断基准.md") for r in report.resources)


def test_package_rejects_dependency_outside_spec(tmp_path):
    allowed = tmp_path / "spec"
    source = allowed / "skills" / "test" / "SKILL.md"
    source.parent.mkdir(parents=True)
    secret = tmp_path / "outside.md"
    secret.write_text("must not be packaged")
    source.write_text("[outside](../../../outside.md)")
    with pytest.raises(ValueError, match="超出允许"):
        _package(source, tmp_path / "package", allowed)


def test_package_rejects_hidden_environment_file_inside_allowed_tree(tmp_path):
    source = tmp_path / "spec" / "skills" / "test" / "SKILL.md"
    secret = tmp_path / "spec" / "skills" / "shared" / ".env"
    source.parent.mkdir(parents=True)
    secret.parent.mkdir(parents=True)
    source.write_text("[private config](../shared/.env)")
    secret.write_text("fixture content")

    with pytest.raises(ValueError, match="隐藏或密钥文件"):
        _package(source, tmp_path / "package", tmp_path)


def test_package_cycles_keep_local_references_readable(tmp_path):
    source = tmp_path / "spec" / "skills" / "test" / "SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text("[first](first.md)")
    (source.parent / "first.md").write_text("[second](second.md)")
    (source.parent / "second.md").write_text("[first](first.md)")
    target = tmp_path / "package"
    _package(source, target, tmp_path)
    assert (target / "SKILL.md").exists()
    assert len(list((target / "references").glob("*.md"))) == 2
