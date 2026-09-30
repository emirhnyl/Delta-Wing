"""OpenFOAM komutlarını yerel kurulumda veya Docker kapsayıcısında çalıştırma.

``openfoam.runner``:
  auto   : yerel OpenFOAM varsa onu, yoksa Docker'ı kullanır
  local  : sistemde kurulu OpenFOAM (etc/bashrc otomatik bulunur)
  docker : ``openfoam.docker_image`` imajı (varsayılan opencfd/openfoam-default,
           Apple Silicon için arm64 sürümü mevcuttur)
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import uuid
from pathlib import Path

DEFAULT_IMAGE = "opencfd/openfoam-default:2512"

BASHRC_CANDIDATES = (
    "/usr/share/openfoam/etc/bashrc",           # Ubuntu/Debian 'openfoam' paketi
    "/usr/lib/openfoam/openfoam*/etc/bashrc",   # openfoam.com deb/rpm paketleri
    "/opt/openfoam*/etc/bashrc",                # openfoam.org paketleri
    "~/OpenFOAM/OpenFOAM-*/etc/bashrc",         # kaynaktan derleme
)

# macOS'ta uygulama Finder'dan açıldığında PATH kısıtlı olabilir
EXTRA_PATHS = ("/opt/homebrew/bin", "/usr/local/bin",
               "/Applications/Docker.app/Contents/Resources/bin", str(Path.home() / ".colima" / "bin"))


def augmented_env() -> dict:
    env = dict(os.environ)
    parts = env.get("PATH", "").split(os.pathsep)
    for p in EXTRA_PATHS:
        if p not in parts and Path(p).exists():
            parts.append(p)
    env["PATH"] = os.pathsep.join(parts)
    env.setdefault("OMPI_ALLOW_RUN_AS_ROOT", "1")
    env.setdefault("OMPI_ALLOW_RUN_AS_ROOT_CONFIRM", "1")
    return env


def which(cmd: str) -> str | None:
    return shutil.which(cmd, path=augmented_env()["PATH"])


def find_bashrc(cfg: dict) -> str | None:
    """Yerel OpenFOAM ortam dosyası; ortam zaten yüklüyse (WM_PROJECT_DIR) None."""
    if cfg["openfoam"].get("bashrc"):
        return str(Path(cfg["openfoam"]["bashrc"]).expanduser())
    if os.environ.get("WM_PROJECT_DIR"):
        return None
    for pat in BASHRC_CANDIDATES:
        hits = sorted(glob.glob(os.path.expanduser(pat)))
        if hits:
            return hits[-1]
    return None


def local_available(cfg: dict) -> bool:
    rc = find_bashrc(cfg)
    if rc:
        return Path(rc).exists()
    return which("simpleFoam") is not None


def docker_available(timeout: float = 8.0) -> bool:
    d = which("docker")
    if not d:
        return False
    try:
        return subprocess.run([d, "info"], capture_output=True, timeout=timeout,
                              env=augmented_env()).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def docker_image_present(image: str) -> bool:
    d = which("docker")
    if not d:
        return False
    try:
        return subprocess.run([d, "image", "inspect", image], capture_output=True, timeout=15,
                              env=augmented_env()).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def resolve_runner(cfg: dict) -> str:
    mode = str(cfg["openfoam"].get("runner", "auto")).lower()
    if mode in ("local", "docker"):
        return mode
    if local_available(cfg):
        return "local"
    if docker_available():
        return "docker"
    return "none"


def use_function_objects(cfg: dict) -> bool:
    """Ubuntu/Debian 'openfoam' (1912) paketinde tüm fonksiyon nesneleri
    'error in IOstream "sha1"' hatasıyla çöker; orada otomatik kapatılır."""
    mode = str(cfg["openfoam"].get("function_objects", "auto")).lower()
    if mode in ("true", "on", "1", "yes"):
        return True
    if mode in ("false", "off", "0", "no"):
        return False
    if resolve_runner(cfg) == "docker":
        return True
    return find_bashrc(cfg) != "/usr/share/openfoam/etc/bashrc"


SOURCE_IN_CONTAINER = ('for f in /usr/lib/openfoam/openfoam*/etc/bashrc /opt/openfoam*/etc/bashrc; '
                       'do [ -f "$f" ] && . "$f" > /dev/null 2>&1 && break; done; ')


def build_command(case: Path, cfg: dict, script: str = "./Allrun"):
    """(argv, env, container_name) - vaka klasöründe script'i çalıştıracak komut."""
    runner = resolve_runner(cfg)
    env = augmented_env()
    case = Path(case).resolve()
    if runner == "docker":
        image = cfg["openfoam"].get("docker_image") or DEFAULT_IMAGE
        name = f"deltawing_{uuid.uuid4().hex[:10]}"
        argv = [which("docker") or "docker", "run", "--rm", "--name", name,
                "--shm-size=1g", "-v", f"{case}:/case", "-w", "/case",
                "-e", "OMPI_ALLOW_RUN_AS_ROOT=1", "-e", "OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1",
                "--entrypoint", "/bin/bash", image, "-c", SOURCE_IN_CONTAINER + script]
        return argv, env, name
    if runner == "local":
        rc = find_bashrc(cfg)
        cmd = f"source {rc} > /dev/null 2>&1; {script}" if rc else script
        return ["bash", "-c", cmd], env, None
    raise RuntimeError("OpenFOAM bulunamadı: Kurulum sayfasından Docker + OpenFOAM kurun "
                       "veya openfoam.runner / openfoam.bashrc ayarlayın.")


def kill_container(name: str) -> None:
    d = which("docker")
    if d and name:
        subprocess.run([d, "kill", name], capture_output=True, env=augmented_env(), timeout=30)
