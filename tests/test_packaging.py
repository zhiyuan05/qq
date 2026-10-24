import re
import tomllib
from pathlib import Path

import yaml

from utils.version import __version__


ROOT = Path(__file__).resolve().parents[1]


def _requirement_name(requirement: str) -> str:
    return re.split(r"[<>=!~\[]", requirement, maxsplit=1)[0].strip().lower()


def test_requirements_and_pyproject_dependencies_stay_in_sync():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project_names = {_requirement_name(item) for item in pyproject["project"]["dependencies"]}
    requirement_names = {
        _requirement_name(line)
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert project_names == requirement_names


def test_package_runtime_and_container_versions_stay_in_sync():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    assert pyproject["project"]["version"] == __version__
    assert f"app.version == '{__version__}'" in workflow


def test_web_assets_are_declared_as_package_data():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]
    assert set(package_data["web"]) == {
        "static/*.css",
        "static/*.js",
        "static/*.svg",
        "templates/*.html",
    }
    for relative_path in (
        "web/templates/login.html",
        "web/templates/chat.html",
        "web/templates/admin.html",
        "web/static/login.css",
        "web/static/login.js",
        "web/static/login-logo.svg",
        "web/static/chat.css",
        "web/static/chat.js",
        "web/static/admin.css",
        "web/static/admin.js",
    ):
        assert (ROOT / relative_path).is_file()


def test_package_discovery_excludes_generated_and_runtime_trees():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    discovery = pyproject["tool"]["setuptools"]["packages"]["find"]

    assert discovery["namespaces"] is False
    assert {
        "tests",
        "tests.*",
        "build",
        "build.*",
        "dist",
        "dist.*",
        "data",
        "data.*",
    }.issubset(discovery["exclude"])
    # A broad ``data*`` pattern also matches the real ``database`` package.
    assert "data*" not in discovery["exclude"]


def test_database_migrations_are_declared_as_package_data():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]

    assert package_data["database"] == ["migrations/*.sql"]
    assert sorted((ROOT / "database" / "migrations").glob("*.sql"))


def test_compose_hardens_chatbot_container():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["chatbot"]
    assert service["read_only"] is True
    assert service["init"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert any(item.startswith("/tmp:rw,noexec,nosuid") for item in service["tmpfs"])
    assert service["build"]["args"] == {
        "UID": "${CHATBOT_UID:-1000}",
        "GID": "${CHATBOT_GID:-1000}",
        "INSTALL_VOICE": "${CHATBOT_INSTALL_VOICE:-false}",
    }
    assert service["ports"] == ["127.0.0.1:${CHATBOT_WEB_PORT:-8080}:8080"]
    assert service["container_name"] == "${CHATBOT_CONTAINER_PREFIX:-}chatbot"
    assert compose["services"]["napcat"]["container_name"] == "${CHATBOT_CONTAINER_PREFIX:-}napcat"
    assert "/api/v1/health/ready" in service["healthcheck"]["test"][-1]


def test_postgres_compose_overlay_has_durable_storage_and_health_gate():
    compose = yaml.safe_load((ROOT / "docker-compose.postgres.yml").read_text(encoding="utf-8"))
    postgres = compose["services"]["postgres"]
    chatbot = compose["services"]["chatbot"]

    assert postgres["image"] == "postgres:16-alpine"
    assert "chatbot-postgres:/var/lib/postgresql/data" in postgres["volumes"]
    assert "pg_isready" in postgres["healthcheck"]["test"][-1]
    assert chatbot["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert chatbot["environment"]["CHATBOT_MULTI_INSTANCE"] == "true"
    assert chatbot["environment"]["CHATBOT_DATABASE_URL"].startswith("postgresql+asyncpg://")


def test_runtime_image_drops_root_privileges_after_copy():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ENV PYTHONDONTWRITEBYTECODE=1" in dockerfile
    assert "COPY --chown=chatbot:chatbot . ." in dockerfile
    assert dockerfile.rindex("USER chatbot:chatbot") > dockerfile.rindex("COPY --chown=chatbot:chatbot . .")
    assert "/api/v1/health/ready" in dockerfile


def test_ci_validates_python_wheel_and_container_builds():
    workflow_path = ROOT / ".github" / "workflows" / "test.yml"
    workflow_text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    assert set(workflow["jobs"]) == {"test", "postgres", "container"}
    assert 'python -m pip wheel --no-deps --no-build-isolation' in workflow_text
    assert "Verify wheel contents" in workflow_text
    assert "Smoke test installed wheel" in workflow_text
    assert "--force-reinstall --no-deps dist/chatbot-*.whl" in workflow_text
    assert "database/migrations/003_session_revision_and_entity_identity.sql" in workflow_text
    assert "docker/setup-buildx-action@v4.1.0" in workflow_text
    assert "docker/build-push-action@v7.3.0" in workflow_text
    assert "assert os.getuid() != 0" in workflow_text
    assert "bash -n deploy.sh" in workflow_text
    assert "tests/test_postgres_integration.py" in workflow_text
    assert "postgres:16-alpine" in workflow_text
    assert "docker-compose.postgres.yml" in workflow_text


def test_deploy_script_uses_generic_llm_key_and_allows_ollama():
    script = (ROOT / "deploy.sh").read_text(encoding="utf-8")
    assert "CHATBOT_LLM_API_KEY" in script
    assert "CHATBOT_WEB_AUTH_TOKEN" in script
    assert "?ollama" in script
    assert "set -Eeuo pipefail" in script
    assert "python -m database.migrate --migration-timeout 300" in script
    assert "--postgres)" in script
    assert "--voice)" in script
    assert "/api/v1/health/ready" in script
