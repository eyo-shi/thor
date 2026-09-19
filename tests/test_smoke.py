"""パッケージが import できることだけを確認するスモークテスト。"""
from __future__ import annotations


def test_import_thor() -> None:
    import thor

    assert thor.__version__


def test_import_subpackages() -> None:
    import thor.analytics  # noqa: F401
    import thor.api  # noqa: F401
    import thor.demo  # noqa: F401
    import thor.ingestion  # noqa: F401
    import thor.router  # noqa: F401
    import thor.semantic  # noqa: F401
    import thor.session  # noqa: F401
    import thor.tools  # noqa: F401
    import thor.transport  # noqa: F401
