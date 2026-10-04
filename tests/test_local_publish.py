"""Exercise the local PowerShell publisher with private Git and fake build work."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from test_cloud_publish import candidate  # noqa: F401; synthetic export only

SOURCE = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows scheduled publisher")


WORKER = r'''
import json, os, shutil, subprocess, sys
from pathlib import Path

arguments = sys.argv[2:]
kind = sys.argv[1]
log = Path(os.environ['FX_LOCAL_LOG'])
previous = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
with log.open('a', encoding='utf-8') as stream:
    stream.write(json.dumps({'kind': kind, 'args': arguments, 'cwd': os.getcwd()}) + '\n')

def git(args, cwd=None):
    return subprocess.run([os.environ['FX_LOCAL_GIT'], *args], cwd=cwd, capture_output=True)

def emit(result):
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    raise SystemExit(result.returncode)

def advance():
    seed = os.environ['FX_LOCAL_SEED']
    remote = os.environ['FX_LOCAL_REMOTE']
    for args in [
        ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
         'commit', '--allow-empty', '-q', '-m', 'concurrent publisher'],
        ['push', '--quiet', '--force', remote, 'HEAD:refs/heads/gh-pages'],
    ]:
        result = git(args, seed)
        if result.returncode:
            emit(result)
    Path(os.environ['FX_LOCAL_RACE']).write_text(git(['rev-parse', 'HEAD'], seed).stdout.decode().strip())

if kind == 'python':
    assert arguments[:1] == ['-m']
    module = arguments[1]
    if module == 'fxdash.web.build':
        target = arguments[arguments.index('--out') + 1]
        shutil.copytree(os.environ['FX_LOCAL_TEMPLATE'], target, dirs_exist_ok=True)
    elif module == 'fxdash.cloud.publish':
        assert arguments[2] == '--candidate'
        sys.path.insert(0, os.environ['FX_LOCAL_SOURCE'])
        from fxdash.cloud import publish
        try:
            print(json.dumps(publish.inspect(arguments[3], secrets=())))
        except Exception as error:
            print(str(error), file=sys.stderr)
            raise SystemExit(2)
    elif module != 'fxdash.narrative.public_delivery':
        raise AssertionError('Unexpected Python module: ' + module)
    raise SystemExit(0)

assert kind == 'git'
scenario = os.environ['FX_LOCAL_SCENARIO']
command = arguments[0]
index = 0
while arguments[index] == '-c':
    index += 2
command = arguments[index]
if command in {'push', 'ls-remote'}:
    assert os.environ['FX_LOCAL_REMOTE'] in arguments
    assert not any(value.startswith(('http:', 'https:', 'ssh:', 'git@')) for value in arguments)
if scenario == 'fail_' + command.replace('-', '_'):
    print('Synthetic native Git failure', file=sys.stderr)
    raise SystemExit(9)
if command == 'rev-parse' and scenario == 'invalid_local_head':
    print('deadbeef')
    raise SystemExit(0)
if command == 'ls-remote' and scenario == 'invalid_remote_head':
    print('deadbeef\trefs/heads/gh-pages')
    raise SystemExit(0)
if command == 'ls-remote' and scenario == 'duplicate_remote_head':
    output = git(arguments)
    sys.stdout.buffer.write(output.stdout + output.stdout)
    raise SystemExit(output.returncode)
if command == 'ls-remote' and scenario == 'fail_post_probe' and any(
        entry['kind'] == 'git' and entry['args'][0] == 'push' for entry in previous):
    print('Synthetic post-push probe failure', file=sys.stderr)
    raise SystemExit(9)
if command == 'push' and scenario == 'race_before_push':
    advance()
result = git(arguments)
if command == 'push' and scenario == 'race_after_push' and result.returncode == 0:
    advance()
emit(result)
'''


class Publisher:
    def __init__(self, tmp_path, template):
        self.root = tmp_path
        self.git = shutil.which("git")
        self.shell = shutil.which("powershell") or shutil.which("pwsh")
        if not self.git or not self.shell:
            pytest.skip("Git and PowerShell are needed for local publisher integration")
        self.repo, self.seed, self.remote = (tmp_path / name for name in ("repo", "seed", "remote.git"))
        self.site, self.log, self.race = (tmp_path / name for name in ("site", "calls.jsonl", "race.txt"))
        (self.repo / "ops").mkdir(parents=True)
        (self.repo / "src/fxdash/web").mkdir(parents=True)
        (self.repo / "src/fxdash/web/build.py").write_text("# Fake build marker\n")
        self.script = self.repo / "ops/publish.ps1"
        self.script.write_bytes((SOURCE / "ops/publish.ps1").read_bytes())
        self.seed.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.upper().startswith(("GIT_", "GCM_", "SSH_"))}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
        settings = [("protocol.allow", "never"), ("protocol.file.allow", "always"),
                    ("core.hooksPath", os.devnull), ("core.attributesFile", os.devnull),
                    ("core.autocrlf", "false"), ("commit.gpgSign", "false")]
        self.env["GIT_CONFIG_COUNT"] = str(len(settings))
        for index, (key, value) in enumerate(settings):
            self.env[f"GIT_CONFIG_KEY_{index}"] = key
            self.env[f"GIT_CONFIG_VALUE_{index}"] = value
        self.real_git("init", "--bare", str(self.remote))
        self.real_git("init", "-q", "-b", "gh-pages", cwd=self.seed)
        self.real_git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                      "commit", "--allow-empty", "-q", "-m", "initial fixture", cwd=self.seed)
        self.initial = self.real_git("rev-parse", "HEAD", cwd=self.seed)
        worker = tmp_path / "worker.py"
        worker.write_text(WORKER, encoding="utf-8")
        tools = tmp_path / "tools"
        tools.mkdir()
        for name, kind in (("git", "git"), ("python", "python")):
            command = subprocess.list2cmdline([sys.executable, str(worker), kind])
            (tools / f"{name}.cmd").write_text(f"@{command} %*\n@exit /b %errorlevel%\n", encoding="utf-8")
        self.python = tools / "python.cmd"
        self.env.update(
            PATH=str(tools) + os.pathsep + self.env.get("PATH", ""),
            FX_LOCAL_GIT=self.git, FX_LOCAL_SEED=str(self.seed), FX_LOCAL_REMOTE=str(self.remote),
            FX_LOCAL_LOG=str(self.log), FX_LOCAL_RACE=str(self.race),
            FX_LOCAL_TEMPLATE=str(template), FX_LOCAL_SOURCE=str(SOURCE / "src"),
        )

    def real_git(self, *args, cwd=None):
        result = subprocess.run([self.git, *args], cwd=cwd or self.root, env=self.env,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def run(self, scenario="normal", *, empty=False, what_if=False, site_argument=None, cwd=None):
        if not empty:
            self.real_git("push", "--quiet", str(self.remote), "HEAD:refs/heads/gh-pages", cwd=self.seed)
        self.env["FX_LOCAL_SCENARIO"] = scenario
        args = [self.shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.script),
                "-Python", str(self.python), "-Site", site_argument or str(self.site), "-Remote", str(self.remote)]
        if what_if:
            args.append("-WhatIf")
        result = subprocess.run(args, cwd=cwd or self.repo, env=self.env, capture_output=True,
                                text=True, errors="replace", timeout=60)
        self.calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.head = self.real_git("ls-remote", str(self.remote), "refs/heads/gh-pages").split("\t")[0]
        self.output = result.stdout + result.stderr
        (self.root / "local-result.json").write_text(json.dumps({
            "scenario": scenario, "empty": empty, "what_if": what_if, "returncode": result.returncode,
            "initial": self.initial, "remote": self.head, "race": self.race.read_text() if self.race.exists() else None,
            "calls": self.calls, "output": self.output,
        }, indent=2), encoding="utf-8")
        return result

    def git_calls(self, command):
        return [row["args"] for row in self.calls if row["kind"] == "git" and row["args"][0] == command]

    def delivered(self):
        return any(row["kind"] == "python" and row["args"][1] == "fxdash.narrative.public_delivery"
                   for row in self.calls)


@pytest.fixture
def publisher(tmp_path, candidate):
    return Publisher(tmp_path, candidate)


@pytest.mark.parametrize("empty", [False, True])
def test_local_push_uses_exact_lease_and_confirms_full_remote_commit(publisher, empty):
    result = publisher.run(empty=empty)
    assert result.returncode == 0, publisher.output
    local = publisher.real_git("rev-parse", "HEAD", cwd=publisher.site)
    assert publisher.head == local and len(local) == 40
    push, = publisher.git_calls("push")
    expected = "" if empty else publisher.initial
    assert f"--force-with-lease=refs/heads/gh-pages:{expected}" in push and "--force" not in push
    assert publisher.delivered() and "publish done" in publisher.output


def test_relative_site_from_another_cwd_uses_one_repository_path(publisher):
    publisher.site = publisher.repo / "custom-site"
    result = publisher.run(site_argument="custom-site", cwd=publisher.root)
    assert result.returncode == 0, publisher.output
    python = [row for row in publisher.calls if row["kind"] == "python"]
    build, inspection = python[:2]
    assert Path(build["args"][build["args"].index("--out") + 1]) == publisher.site
    assert Path(inspection["args"][3]) == publisher.site
    assert all(Path(row["cwd"]) == publisher.site for row in publisher.calls if row["kind"] == "git")
    assert publisher.head == publisher.real_git("rev-parse", "HEAD", cwd=publisher.site)
    assert not (publisher.root / "custom-site").exists()


def test_remote_advance_between_probe_and_push_is_preserved(publisher):
    result = publisher.run("race_before_push")
    assert result.returncode != 0, publisher.output
    assert publisher.head == publisher.race.read_text()
    assert not publisher.delivered() and "publish done" not in publisher.output


def test_post_push_remote_advance_is_reported_as_unconfirmed(publisher):
    result = publisher.run("race_after_push")
    assert result.returncode != 0, publisher.output
    assert publisher.head == publisher.race.read_text()
    assert not publisher.delivered() and "publish done" not in publisher.output


@pytest.mark.parametrize("scenario", [
    "fail_init", "fail_symbolic_ref", "fail_add", "fail_commit", "fail_rev_parse", "fail_ls_remote",
    "invalid_local_head", "invalid_remote_head", "duplicate_remote_head",
])
def test_native_git_failure_or_invalid_identity_stops_before_push(publisher, scenario):
    result = publisher.run(scenario)
    assert result.returncode != 0, publisher.output
    assert not publisher.git_calls("push") and publisher.head == publisher.initial
    assert not publisher.delivered() and "publish done" not in publisher.output


def test_failed_post_push_probe_never_reports_confirmed_publication(publisher):
    result = publisher.run("fail_post_probe")
    assert result.returncode != 0, publisher.output
    assert len(publisher.git_calls("push")) == 1
    assert not publisher.delivered() and "publish done" not in publisher.output


def test_what_if_builds_and_validates_without_git_or_delivery(publisher):
    result = publisher.run(what_if=True)
    assert result.returncode == 0, publisher.output
    assert [row["args"][1] for row in publisher.calls] == ["fxdash.web.build", "fxdash.cloud.publish"]
    assert publisher.head == publisher.initial and not publisher.delivered()


def test_invalid_build_identity_cannot_reach_git_or_public_probe(publisher):
    path = Path(publisher.env["FX_LOCAL_TEMPLATE"]) / "build.json"
    manifest = json.loads(path.read_bytes())
    manifest["data_version"] = "another-contract"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = publisher.run()
    assert result.returncode != 0, publisher.output
    assert not any(row["kind"] == "git" for row in publisher.calls)
    assert publisher.head == publisher.initial and not publisher.delivered()
    assert "publish done" not in publisher.output
