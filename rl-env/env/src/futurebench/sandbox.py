"""Docker images and containers for episodes.

Two images per task, built once and cached by tag:
  agent  the snapshot tree at /workspace with its dependencies in /opt/venv. Episodes run in it with
         --network none and the host workspace mounted over /workspace.
  score  upstream HEAD's tree at /future with HEAD's dependencies. Only the scorer uses it, on a
         copy outside the episode, so nothing from after the snapshot ever enters an episode.
Files that an install generates inside package code (e.g. a hatch-vcs _version.py) are saved in
/opt/generated and copied into any mounted tree that lacks them.

Images are built with `docker run` + `docker commit` rather than a Dockerfile so the host's uv
binary and proxy settings (HTTPS_PROXY, a CA bundle) can be lent to the build without being baked in.
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# the full (not -slim) image, so agents get git; mirror.gcr.io because Docker Hub rate-limits anonymous pulls
BASE_IMAGE = os.environ.get("FUTUREBENCH_BASE_IMAGE", "mirror.gcr.io/library/python:3.13")


def base_tag():
    """Short, stable id of the base image name, so images built on another base get other tags."""
    import hashlib
    return hashlib.sha1(BASE_IMAGE.encode()).hexdigest()[:6]
VENV = "/opt/venv"
GENERATED = "/opt/generated"
MOUNTS = {"agent": "/workspace", "score": "/future"}

INSTALL = r"""
set -e
W="$1"; shift
mkdir -p "$W" /opt/generated
cp -a /src/. "$W"/
cd "$W"
find . -type f | sort > /tmp/before
uv venv -q -p python3 /opt/venv
export VIRTUAL_ENV=/opt/venv SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0 UV_LINK_MODE=copy
uv pip install -q pytest "$@"
ok=0
for g in test tests dev testing; do
  if uv pip install -q -e . --group "$g" 2>/dev/null; then ok=1; break; fi
done
[ "$ok" = 1 ] || uv pip install -q -e .
# optional extras (e.g. python-dotenv's "cli" = click), each on its own so one bad extra can't block the rest
for x in $(python3 -c 'import tomllib; print(" ".join(tomllib.load(open("pyproject.toml","rb")).get("project",{}).get("optional-dependencies",{})))' 2>/dev/null); do
  uv pip install -q -e ".[$x]" 2>/dev/null || echo "extra $x not installable"
done
for req in requirements*test*.txt */requirements*test*.txt requirements/test*.txt; do
  [ -f "$req" ] && uv pip install -q -r "$req"
done
find . -type f -not -path '*/__pycache__/*' -not -path './.git/*' -not -name '*.pyc' | sort > /tmp/after
comm -13 /tmp/before /tmp/after | while read -r f; do
  case "$f" in *.egg-info/*) continue;; esac
  mkdir -p "/opt/generated/$(dirname "$f")"; cp -a "$f" "/opt/generated/$f"
done
echo "generated: $(comm -13 /tmp/before /tmp/after | grep -vc egg-info || true)"
"""

# copy generated files into the mounted tree without overwriting, then run the command there
ENTRY = ('W="$1"; shift; cd /opt/generated && find . -type f | while read -r f; do '
         '[ -e "$W/$f" ] || { mkdir -p "$W/$(dirname "$f")"; cp -a "$f" "$W/$f"; }; done; '
         'cd "$W" && exec "$@"')


def docker(*args, check=True, timeout=None, capture=True):
    p = subprocess.run(["docker", *args], capture_output=capture, text=True, timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(f"docker {' '.join(args[:3])} failed ({p.returncode}): {(p.stderr or '')[-2000:]}")
    return p


def image_exists(tag):
    return docker("image", "inspect", tag, check=False).returncode == 0


def _proxy_args():
    args = []
    for k in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy"):
        if os.environ.get(k):
            args += ["-e", f"{k}={os.environ[k]}"]
    ca = os.environ.get("FUTUREBENCH_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if not ca and Path("/root/.ccr/ca-bundle.crt").exists():
        ca = "/root/.ccr/ca-bundle.crt"
    if ca:
        args += ["-v", f"{ca}:/fb-ca.crt:ro", "-e", "SSL_CERT_FILE=/fb-ca.crt", "-e", "UV_NATIVE_TLS=1"]
    return args


def build_image(tag: str, tree: Path, kind: str, extra_deps=(), log=print):
    """Install `tree` into a fresh base container at MOUNTS[kind] and commit it as `tag`."""
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is needed on the host to build images (it is lent to the build, not baked in)")
    if not image_exists(BASE_IMAGE):
        docker("pull", "-q", BASE_IMAGE, timeout=600)
    name = f"fb-build-{os.getpid()}-{kind}"
    docker("rm", "-f", name, check=False)
    docker("create", "--name", name, "--network", "host", *_proxy_args(),
           "-v", f"{tree}:/src:ro", "-v", f"{uv}:/usr/local/bin/uv:ro",
           BASE_IMAGE, "sh", "-c", INSTALL, "install", MOUNTS[kind], *extra_deps)
    try:
        p = docker("start", "-a", name, check=False, timeout=3600)
        if p.returncode:
            raise RuntimeError(f"building {tag} failed:\n{(p.stdout + p.stderr)[-3000:]}")
        log(f"built {tag}: {p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ''}")
        docker("commit", "--change", f"ENV PATH={VENV}/bin:/usr/local/bin:/usr/bin:/bin",
               "--change", f"ENV VIRTUAL_ENV={VENV}", "--change", "ENV PYTHONDONTWRITEBYTECODE=1",
               "--change", f"WORKDIR {MOUNTS[kind]}", "--change", "CMD [\"sleep\", \"infinity\"]", name, tag)
    finally:
        docker("rm", "-f", name, check=False)


def _user_args():
    return ["--user", f"{os.getuid()}:{os.getgid()}"] if os.getuid() != 0 else []


def run_in(image: str, kind: str, tree: Path, cmd: list[str], timeout=2400, network="none"):
    """Run cmd in a throwaway container with `tree` mounted at the image's code path."""
    mount = MOUNTS[kind]
    p = subprocess.run(["docker", "run", "--rm", "--network", network, *_user_args(),
                        "-v", f"{tree}:{mount}", image, "sh", "-c", ENTRY, "entry", mount, *cmd],
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout + p.stderr


class Container:
    """A long-lived episode container: --network none, workspace mounted at /workspace."""

    def __init__(self, image: str, workspace: Path, name: str):
        self.image, self.workspace, self.name = image, workspace, name

    def start(self):
        docker("rm", "-f", self.name, check=False)
        docker("run", "-d", "--name", self.name, "--network", "none", *_user_args(),
               "-v", f"{self.workspace}:{MOUNTS['agent']}", self.image,
               "sh", "-c", ENTRY, "entry", MOUNTS["agent"], "sleep", "infinity")
        return self

    def exec(self, command: str, timeout=1800):
        """Run a shell command inside the episode container; returns (exit code, combined output)."""
        p = subprocess.run(["docker", "exec", "-w", MOUNTS["agent"], self.name, "sh", "-c", command],
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout + p.stderr

    def stop(self):
        docker("rm", "-f", self.name, check=False)


def temp_dir(prefix="fb-"):
    root = os.environ.get("FUTUREBENCH_TMP")
    return Path(tempfile.mkdtemp(prefix=prefix, dir=root))
