"""Çalışma ortamı kontrolü ve kurulum otomasyonu (özellikle macOS / Apple Silicon).

macOS'ta önerilen yol, yönetici şifresi gerektirmeyen **Colima** + Docker CLI'dır:

    Homebrew  ->  brew install colima docker  ->  colima start (vz, virtiofs)
              ->  docker pull opencfd/openfoam-default  ->  kısa test CFD

Homebrew'un kendi kurulumu yönetici şifresi istediği için Terminal penceresinde
açılır. Docker Desktop zaten kuruluysa o da algılanır ve kullanılır.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
from pathlib import Path

from . import runner

HOMEBREW_INSTALL = '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'


def _run(argv, timeout=15) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=runner.augmented_env())
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


def system_info() -> dict:
    info = {"os": platform.system(), "machine": platform.machine(), "cpus": os.cpu_count() or 2,
            "python": platform.python_version(), "mac_version": None, "memory_gb": None}
    if info["os"] == "Darwin":
        info["mac_version"] = platform.mac_ver()[0]
        rc, out = _run(["sysctl", "-n", "hw.memsize"])
        if rc == 0 and out.isdigit():
            info["memory_gb"] = int(out) / 1024**3
    else:
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal"):
                    info["memory_gb"] = int(line.split()[1]) / 1024**2
        except OSError:
            pass
    return info


def recommended_resources(info: dict | None = None) -> dict:
    info = info or system_info()
    cpus = int(info["cpus"])
    mem = info.get("memory_gb") or 8
    return {"cpu": max(2, cpus - 2), "memory": int(max(4, min(mem * 0.5, 16))), "disk": 40}


def status(cfg: dict) -> dict:
    info = system_info()
    brew = runner.which("brew")
    docker = runner.which("docker")
    colima = runner.which("colima")
    image = cfg["openfoam"].get("docker_image") or runner.DEFAULT_IMAGE
    st = {
        "system": info,
        "is_mac": info["os"] == "Darwin",
        "homebrew": {"ok": bool(brew), "path": brew},
        "docker_cli": {"ok": bool(docker), "path": docker},
        "colima": {"ok": bool(colima), "path": colima, "running": False, "detail": ""},
        "docker_desktop": {"ok": Path("/Applications/Docker.app").exists()},
        "docker_engine": {"ok": False, "detail": ""},
        "image": {"ok": False, "name": image},
        "local_openfoam": {"ok": runner.local_available(cfg), "bashrc": runner.find_bashrc(cfg)},
        "runner": None,
        "recommended": recommended_resources(info),
    }
    if colima:
        rc, out = _run([colima, "status"], timeout=20)
        st["colima"]["running"] = rc == 0
        st["colima"]["detail"] = out[-400:]
    if docker:
        rc, out = _run([docker, "info", "--format", "{{.ServerVersion}} | CPU {{.NCPU}} | RAM {{.MemTotal}}"], timeout=15)
        st["docker_engine"]["ok"] = rc == 0
        if rc == 0:
            parts = out.split("|")
            try:
                mem = int(parts[2].split()[-1]) / 1024**3
                out = f"Docker {parts[0].strip()} · {parts[1].strip()} · {mem:.1f} GB RAM"
            except (IndexError, ValueError):
                pass
        st["docker_engine"]["detail"] = out[-400:]
        if rc == 0:
            st["image"]["ok"] = runner.docker_image_present(image)
    st["runner"] = runner.resolve_runner(cfg)
    st["ready"] = st["runner"] == "local" or (st["docker_engine"]["ok"] and st["image"]["ok"])
    return st


# --------------------------------------------------------------------------- eylemler


def open_terminal_with(command: str) -> str:
    """macOS Terminal'de komut çalıştırır (şifre gerektiren adımlar için)."""
    script = f'tell application "Terminal"\n activate\n do script {json.dumps(command)}\nend tell'
    rc, out = _run(["osascript", "-e", script], timeout=20)
    if rc != 0:
        raise RuntimeError(f"Terminal açılamadı: {out}")
    return "Terminal açıldı. Kurulumu orada tamamlayın (Mac şifreniz istenecek), sonra 'Durumu yenile'ye basın."


def install_homebrew(job) -> str:
    if platform.system() != "Darwin":
        raise RuntimeError("Homebrew otomatik kurulumu yalnızca macOS içindir")
    msg = open_terminal_with(HOMEBREW_INSTALL + " && echo && echo 'Homebrew kuruldu. Bu pencereyi kapatabilirsiniz.'")
    job.log(msg)
    return msg


def install_colima(job) -> str:
    brew = runner.which("brew")
    if not brew:
        raise RuntimeError("Önce Homebrew kurulmalı (Homebrew'u kur düğmesi)")
    job.set(0.05, "Colima ve Docker CLI kuruluyor (brew)")
    env = runner.augmented_env()
    env["HOMEBREW_NO_AUTO_UPDATE"] = "1"
    rc = job.run_cmd([brew, "install", "colima", "docker"], env=env, timeout=3600)
    if rc != 0:
        raise RuntimeError("brew install colima docker başarısız")
    return "Colima ve Docker CLI kuruldu"


def start_colima(job, cpu: int, memory: int, disk: int = 40) -> str:
    colima = runner.which("colima")
    if not colima:
        raise RuntimeError("Colima kurulu değil")
    job.set(0.1, "Docker sanal makinesi başlatılıyor (colima)")
    rc, _ = _run([colima, "status"], timeout=20)
    if rc == 0:
        job.log("Colima çalışıyor; yeni kaynak ayarları için yeniden başlatılıyor")
        job.run_cmd([colima, "stop"], env=runner.augmented_env(), timeout=300)
    argv = [colima, "start", "--cpu", str(cpu), "--memory", str(memory), "--disk", str(disk),
            "--mount", f"{Path.home()}:w"]
    mac = platform.mac_ver()[0]
    if mac and int(mac.split(".")[0]) >= 13:
        argv += ["--vm-type", "vz", "--mount-type", "virtiofs"]  # OpenFOAM imajı arm64 yerel
    rc = job.run_cmd(argv, env=runner.augmented_env(), timeout=1800)
    if rc != 0:
        raise RuntimeError("colima start başarısız (günlüğe bakın)")
    return f"Colima çalışıyor ({cpu} CPU, {memory} GB RAM)"


def stop_colima(job) -> str:
    colima = runner.which("colima")
    if not colima:
        raise RuntimeError("Colima kurulu değil")
    job.run_cmd([colima, "stop"], env=runner.augmented_env(), timeout=300)
    return "Colima durduruldu"


def open_docker_desktop(job) -> str:
    rc, out = _run(["open", "-a", "Docker"])
    if rc != 0:
        raise RuntimeError(out)
    return "Docker Desktop açılıyor; motor hazır olunca durumu yenileyin."


def pull_image(job, image: str) -> str:
    docker = runner.which("docker")
    if not docker:
        raise RuntimeError("Docker CLI bulunamadı")
    job.set(0.05, f"OpenFOAM imajı indiriliyor: {image}")
    layers: dict[str, str] = {}

    def on_line(line: str):
        parts = line.split(":", 1)
        if len(parts) == 2 and len(parts[0].strip()) == 12:
            layers[parts[0].strip()] = parts[1].strip()
            done = sum(1 for v in layers.values() if v.startswith(("Pull complete", "Already exists")))
            job.set(0.05 + 0.9 * done / max(len(layers), 1))

    rc = job.run_cmd([docker, "pull", image], env=runner.augmented_env(), timeout=7200, on_line=on_line)
    if rc != 0:
        raise RuntimeError("docker pull başarısız")
    return f"{image} hazır"


def test_openfoam(job, cfg: dict, work_dir: Path) -> dict:
    """Çok kaba bir ağla kısa bir CFD koşusu: tüm zincirin çalıştığını doğrular."""
    import copy
    import shutil

    from .geometry import Wing
    from .openfoam import run_openfoam

    c = copy.deepcopy(cfg)
    c["openfoam"].update({"base_cell_size": 0.6, "surface_level": [3, 4], "feature_level": 4,
                          "near_level": 2, "wake_level": 1, "iterations": 60, "write_interval": 60,
                          "average_last": 10, "n_procs": min(2, int(c["openfoam"].get("n_procs", 2)))})
    case = Path(work_dir) / "setup_test"
    if case.exists():
        shutil.rmtree(case)
    job.set(0.1, f"Test CFD çalıştırılıyor ({runner.resolve_runner(c)})")
    res = run_openfoam(Wing.from_config(c), c, 5.0, case, cancel_event=job.cancel_event, timeout=1800)
    job.log(f"Test tamamlandı: CL={res['CL']:.4f} CD={res['CD']:.5f} (çok kaba ağ, yalnızca doğrulama)")
    return res
