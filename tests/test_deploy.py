"""The deploy files agree with the app: every setting the app reads is in render.yaml,
the start command upgrades the database first, and the GitHub timer calls the right place."""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
RENDER = (ROOT / "render.yaml").read_text(encoding="utf-8")
FACTORY = (ROOT / "shiromoto" / "__init__.py").read_text(encoding="utf-8")


def test_every_environment_setting_is_in_render_yaml():
    read = set(re.findall(r'os\.environ\.get\("([A-Z_]+)"', FACTORY)) - {"RENDER"}
    listed = set(re.findall(r"- key: ([A-Z_]+)", RENDER))
    assert read - listed == set(), "add these to render.yaml"
    assert listed - read == set(), "render.yaml lists settings the app doesn't read"


def test_the_database_is_upgraded_before_the_app_starts():
    start = re.search(r"startCommand: (.+)", RENDER).group(1)
    assert start.startswith("flask --app shiromoto upgrade-db && gunicorn")
    assert "healthCheckPath: /healthz" in RENDER


def test_secrets_are_never_written_in_render_yaml():
    for key in ("DATABASE_URL", "MAIL_PASSWORD", "VAPID_PRIVATE_KEY"):
        block = RENDER.split(f"- key: {key}")[1].split("- key:")[0]
        assert "sync: false" in block and "value:" not in block


def test_the_timer_calls_the_tick_endpoint_with_its_key():
    workflow = (ROOT / ".github" / "workflows" / "tick.yml").read_text(encoding="utf-8")
    assert '- cron: "*/15 * * * *"' in workflow
    assert "X-Tick-Key: $TICK_KEY" in workflow and "secrets.TICK_URL" in workflow


def test_requirements_cover_what_the_app_imports():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for package in ("flask", "sqlalchemy", "alembic", "psycopg", "pillow", "pywebpush", "tzdata", "gunicorn"):
        assert package in requirements, package
