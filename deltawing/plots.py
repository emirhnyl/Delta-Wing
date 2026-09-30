"""Geometri ve sonuç grafikleri (matplotlib, PNG)."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .geometry import DeltaWing  # noqa: E402

# Sabit sıralı kategorik renkler (seri kimliği; sıraya göre atanır, döngü yapılmaz)
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


def _style(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(INK2)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.xaxis.label.set_color(INK)
    ax.yaxis.label.set_color(INK)
    ax.title.set_color(INK)


def plot_geometry(wing: DeltaWing, path: str | Path) -> Path:
    fig = plt.figure(figsize=(12, 7.5), facecolor=SURFACE)
    s = wing.semi_span
    # --- planform (tam kanat, üstten)
    ax = fig.add_subplot(2, 2, 1)
    ys = np.linspace(-s, s, 201)
    le = wing.x_le(ys)
    te = le + wing.chord(ys)
    ax.fill_between(ys, le, te, color=SERIES[0], alpha=0.18, linewidth=0)
    ax.plot(ys, le, color=SERIES[0], lw=2)
    ax.plot(ys, te, color=SERIES[0], lw=2)
    ax.plot([s, s], [wing.x_le(s), wing.x_le(s) + wing.tip_chord], color=SERIES[0], lw=2)
    ax.plot([-s, -s], [wing.x_le(s), wing.x_le(s) + wing.tip_chord], color=SERIES[0], lw=2)
    ax.plot([wing.mac_y, wing.mac_y], [wing.mac_x_le, wing.mac_x_le + wing.mac],
            color=SERIES[1], lw=2, label=f"OAV = {wing.mac:.3f} m")
    ax.invert_yaxis()
    ax.set_aspect("equal")
    ax.set_xlabel("y [m]")
    ax.set_ylabel("x [m]")
    ax.set_title("Planform (üstten görünüş)", fontsize=11)
    ax.legend(frameon=False, fontsize=9)
    _style(ax)

    # --- kesitler
    ax = fig.add_subplot(2, 2, 2)
    for k, (eta, lab) in enumerate(((0.0, "kök"), (0.5, "%50 açıklık"), (0.95, "%95 açıklık"))):
        sec = wing.section(eta * s)
        x = np.concatenate([sec.x[::-1], sec.x[1:]])
        z = np.concatenate([sec.y_upper[::-1], sec.y_lower[1:]])
        ax.plot(x, z, color=SERIES[k], lw=2, label=f"{lab}  t/c={sec.max_thickness:.3f}")
    ax.set_aspect("equal")
    ax.set_xlabel("x/c")
    ax.set_ylabel("z/c")
    ax.set_title("Airfoil kesitleri (birim veter)", fontsize=11)
    ax.set_ylim(-0.12, 0.12)
    ax.legend(frameon=False, fontsize=9, loc="upper center", bbox_to_anchor=(0.5, -0.25), ncol=3)
    _style(ax)

    # --- 3B yüzey
    ax = fig.add_subplot(2, 2, 3, projection="3d")
    v, t = wing.surface_mesh(24, 0.02)
    for sign in (1, -1):
        vv = v.copy()
        vv[:, 1] *= sign
        ax.plot_trisurf(vv[:, 0], vv[:, 1], vv[:, 2], triangles=t, color=SERIES[0],
                        alpha=0.85, linewidth=0, shade=True)
    r = max(wing.root_chord, s) * 0.6
    cx = 0.5 * wing.root_chord
    ax.set_xlim(cx - r, cx + r)
    ax.set_ylim(-r, r)
    ax.set_zlim(-r * 0.5, r * 0.5)
    ax.set_box_aspect((1, 1, 0.5))
    ax.view_init(elev=25, azim=-130)
    ax.set_title("3B geometri", fontsize=11, color=INK)
    ax.set_facecolor(SURFACE)

    # --- özet tablo
    ax = fig.add_subplot(2, 2, 4)
    ax.axis("off")
    sm = wing.summary()
    rows = [
        ("Açıklık b", f"{sm['span_m']:.3f} m"),
        ("Kök veteri", f"{sm['root_chord_m']:.3f} m"),
        ("Uç veteri", f"{sm['tip_chord_m']:.3f} m"),
        ("Alan S", f"{sm['area_m2']:.4f} m²"),
        ("Açıklık oranı AR", f"{sm['aspect_ratio']:.3f}"),
        ("OAV (MAC)", f"{sm['mac_m']:.4f} m"),
        ("Hücum kenarı ok açısı", f"{sm['le_sweep_deg']:.2f}°"),
        ("Firar kenarı ok açısı", f"{sm['te_sweep_deg']:.2f}°"),
        ("Kök / uç t/c", f"{sm['root_t_c']:.3f} / {sm['tip_t_c']:.3f}"),
        ("Hacim", f"{sm['volume_m3'] * 1000:.2f} L"),
    ]
    tb = ax.table(cellText=rows, colLabels=["Parametre", "Değer"], loc="center", cellLoc="left")
    tb.auto_set_font_size(False)
    tb.set_fontsize(10)
    tb.scale(1, 1.5)
    for (i, _), cell in tb.get_celld().items():
        cell.set_edgecolor(GRID)
        cell.set_facecolor(SURFACE if i else "#f0efeb")
    fig.tight_layout()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130, facecolor=SURFACE)
    plt.close(fig)
    return path


def plot_polars(series: dict[str, list[dict]], path: str | Path, title: str = "") -> Path:
    """series: {"VLM + Polhamus": [result, ...], "OpenFOAM RANS": [...]}"""
    fig, axs = plt.subplots(2, 2, figsize=(11, 8), facecolor=SURFACE)
    panels = [
        (axs[0, 0], "alpha_deg", "CL", "Hücum açısı α [°]", "CL", "Kaldırma katsayısı"),
        (axs[0, 1], "alpha_deg", "CD", "Hücum açısı α [°]", "CD", "Sürükleme katsayısı"),
        (axs[1, 0], "CD", "CL", "CD", "CL", "Sürükleme polari"),
        (axs[1, 1], "alpha_deg", "L_over_D", "Hücum açısı α [°]", "L/D", "Kaldırma / sürükleme"),
    ]
    for ax, xk, yk, xl, yl, tt in panels:
        for k, (name, rs) in enumerate(series.items()):
            rs = sorted(rs, key=lambda r: r["alpha_deg"])
            if not rs:
                continue
            ax.plot([r[xk] for r in rs], [r[yk] for r in rs], color=SERIES[k % len(SERIES)],
                    lw=2, marker="o", ms=6, label=name)
        ax.set_xlabel(xl)
        ax.set_ylabel(yl)
        ax.set_title(tt, fontsize=11)
        _style(ax)
    if len(series) > 1:
        axs[0, 0].legend(frameon=False, fontsize=9)
    if title:
        fig.suptitle(title, color=INK)
    fig.tight_layout()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130, facecolor=SURFACE)
    plt.close(fig)
    return path


def plot_history(history: list[dict], path: str | Path, objective: str) -> Path:
    ok = [h for h in history if h.get("ok")]
    fig, ax = plt.subplots(figsize=(8, 4.5), facecolor=SURFACE)
    if ok:
        i = np.array([h["eval"] for h in ok])
        f = np.array([h["f"] for h in ok])
        ax.scatter(i, f, s=10, color=GRID, edgecolors=INK2, linewidths=0.3, label="değerlendirme")
        ax.plot(i, np.minimum.accumulate(f), color=SERIES[0], lw=2, label="en iyi")
        lo, hi = np.percentile(f, [0, 90])
        ax.set_ylim(lo - 0.05 * abs(hi - lo), hi + 0.05 * abs(hi - lo))
    ax.set_xlabel("Değerlendirme #")
    ax.set_ylabel(f"Amaç (+ceza) — {objective}")
    ax.set_title("Optimizasyon yakınsaması", fontsize=11)
    ax.legend(frameon=False, fontsize=9)
    _style(ax)
    fig.tight_layout()
    fig.savefig(path, dpi=130, facecolor=SURFACE)
    plt.close(fig)
    return Path(path)
