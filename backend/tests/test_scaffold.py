import json
import shutil
import time
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_can_generate_next_migration(tmp_path):
    source = Path(__file__).resolve().parents[1] / "migrations"
    target = tmp_path / "migrations"
    shutil.copytree(source, target)
    config = Config()
    config.set_main_option("script_location", str(target))
    revision = ScriptDirectory.from_config(config).generate_revision("test_next", "next schema")
    assert revision is not None
    assert Path(revision.path).is_file()


def test_loop_health_requires_recent_success(monkeypatch, tmp_path):
    from flowforge import process

    monkeypatch.setattr(process, "HEALTH_DIR", tmp_path)
    path = process.health_path("worker")
    assert not process.healthy_loop("worker")
    path.write_text(json.dumps({"updated_at": time.time()}))
    assert process.healthy_loop("worker")
    path.write_text(json.dumps({"updated_at": time.time() - 30}))
    assert not process.healthy_loop("worker")
    path.write_text("invalid")
    assert not process.healthy_loop("worker")


def test_health_module_does_not_import_infrastructure():
    import subprocess
    import sys

    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import flowforge.process; "
            "assert 'sqlalchemy' not in sys.modules; assert 'aio_pika' not in sys.modules",
        ],
        check=True,
    )
