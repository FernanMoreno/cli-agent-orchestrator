"""Built UI catch-all must be registered after every prepared-plan API route."""

import os
import subprocess
import sys
from pathlib import Path


def test_built_ui_mount_does_not_shadow_prepared_plan_routes(tmp_path):
    package = tmp_path / "package"
    (package / "web_ui").mkdir(parents=True)
    (package / "web_ui" / "index.html").write_text("fixture web")
    script = """
import importlib.resources
from pathlib import Path
import sys
from starlette.routing import Match
original = importlib.resources.files
importlib.resources.files = lambda package: Path(sys.argv[1]) if package == 'cli_agent_orchestrator' else original(package)
from cli_agent_orchestrator.api.main import app
for method,path,name in [('POST','/workflows/plans:prepare','prepare_workflow_plan_endpoint'),('GET','/workflows/plans/'+'a'*32,'review_workflow_plan_endpoint')]:
    scope={'type':'http','method':method,'path':path,'root_path':'','headers':[]}
    route=next(route for route in app.router.routes if route.matches(scope)[0] == Match.FULL)
    assert route.name == name, (route.name, name)
assert app.router.routes[-1].name == 'web'
"""
    env = {**os.environ, "CAO_HOME_DIR": str(tmp_path / "cao-home")}
    result = subprocess.run(
        [sys.executable, "-c", script, str(package)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, "Built UI shadows a prepared-plan API route or import failed"
