"""OpenFOAM (openfoam.com / ESI sürümleri, v1912+) ile 3B RANS CFD vakası.

İş akışı
--------
1. Kanat STL'i yazılır (yarı model, kök kesiti simetri düzlemini keser).
2. blockMesh ile dikdörtgen arka plan ağı, surfaceFeatureExtract ile keskin
   kenarlar, snappyHexMesh ile kanat etrafında hex-baskın ağ.
3. simpleFoam (sıkıştırılamaz, sürekli, k-omega SST) çözer.
4. ``forceCoeffs`` / ``forces`` fonksiyon nesneleri kaldırma ve sürükleme
   kuvvetlerini yazar; Python tarafı bunları okuyup tam kanada ölçekler.

Hücum açısı serbest akış vektörü ile verilir (geometri döndürülmez); tüm dış
sınırlar ``freestream`` koşulu kullandığından akış her yönden girip çıkabilir.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np

from .geometry import DeltaWing, write_stl

HEADER = """/*--------------------------------*- C++ -*----------------------------------*\\
  deltawing otomatik vaka üreticisi tarafından oluşturuldu - elle düzenlemeyin
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       {cls};
    location    "{loc}";
    object      {obj};
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

"""


def _write(case: Path, rel: str, body: str, cls: str = "dictionary") -> None:
    path = case / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    loc, obj = str(Path(rel).parent), Path(rel).name
    path.write_text(HEADER.format(cls=cls, loc=loc, obj=obj) + body.strip() + "\n\n// ************************************************************************* //\n")


def _vec(v) -> str:
    return "(" + " ".join(f"{float(c):.8g}" for c in v) + ")"


def flow_vectors(alpha_deg: float):
    a = math.radians(alpha_deg)
    drag_dir = np.array([math.cos(a), 0.0, math.sin(a)])
    lift_dir = np.array([-math.sin(a), 0.0, math.cos(a)])
    return drag_dir, lift_dir


class OpenFOAMCase:
    """Tek bir hücum açısı için tam OpenFOAM vakası."""

    def __init__(self, wing: DeltaWing, cfg: dict, alpha_deg: float, case_dir: str | Path):
        self.wing = wing
        self.cfg = cfg
        self.of = cfg["openfoam"]
        self.alpha = float(alpha_deg)
        self.case = Path(case_dir)

    # --------------------------------------------------------------- derived
    @property
    def U(self) -> float:
        return float(self.cfg["flow"]["velocity"])

    @property
    def nu(self) -> float:
        return float(self.cfg["flow"]["kinematic_viscosity"])

    @property
    def rho(self) -> float:
        return float(self.cfg["flow"]["density"])

    def turbulence_inflow(self):
        ti = float(self.of["turbulence_intensity"])
        k = 1.5 * (self.U * ti) ** 2
        omega = k / (self.nu * float(self.of["viscosity_ratio"]))
        return k, omega

    def domain(self):
        c = self.wing.root_chord
        d = self.of["domain"]
        s = self.wing.semi_span
        zmid = self.wing.z_dihedral(s) * 0.5
        xmin, xmax = -d["upstream"] * c, c + d["downstream"] * c
        ymin, ymax = 0.0, s + d["lateral"] * c
        zmin, zmax = zmid - d["vertical"] * c, zmid + d["vertical"] * c
        return np.array([xmin, ymin, zmin]), np.array([xmax, ymax, zmax])

    # --------------------------------------------------------------- writers
    def write(self) -> Path:
        case = self.case
        if case.exists():
            shutil.rmtree(case)
        (case / "constant" / "triSurface").mkdir(parents=True)
        g = self.cfg["geometry"]
        # kök kesitini y<0'a uzat: snappy simetri düzlemini kanadın içinde keser
        ext = 0.02 * self.wing.root_chord
        v, t = self.wing.surface_mesh(int(g["n_span"]), float(g["min_tip_chord_ratio"]), ext)
        write_stl(case / "constant" / "triSurface" / "wing.stl", v, t, "wing")
        self.bbox_wing = (v.min(axis=0), v.max(axis=0))

        self._block_mesh()
        self._feature_extract()
        self._snappy()
        self._control_dict()
        self._schemes_solution()
        self._constant()
        self._initial_fields()
        self._decompose()
        self._allrun()
        meta = {
            "alpha_deg": self.alpha,
            "velocity": self.U,
            "density": self.rho,
            "kinematic_viscosity": self.nu,
            "Aref_half_m2": 0.5 * self.wing.area,
            "Aref_full_m2": self.wing.area,
            "lRef_m": self.wing.mac,
            "wing": self.wing.summary(),
        }
        (case / "case_info.json").write_text(json.dumps(meta, indent=2))
        return case

    def _block_mesh(self):
        lo, hi = self.domain()
        h = float(self.of["base_cell_size"]) * self.wing.root_chord
        n = np.maximum(np.ceil((hi - lo) / h).astype(int), 1)
        x0, y0, z0 = lo
        x1, y1, z1 = hi
        _write(self.case, "system/blockMeshDict", f"""
scale 1;

vertices
(
    ({x0} {y0} {z0})
    ({x1} {y0} {z0})
    ({x1} {y1} {z0})
    ({x0} {y1} {z0})
    ({x0} {y0} {z1})
    ({x1} {y0} {z1})
    ({x1} {y1} {z1})
    ({x0} {y1} {z1})
);

blocks
(
    hex (0 1 2 3 4 5 6 7) ({n[0]} {n[1]} {n[2]}) simpleGrading (1 1 1)
);

edges ();

boundary
(
    farfield
    {{
        type patch;
        faces
        (
            (0 4 7 3)
            (1 2 6 5)
            (3 7 6 2)
            (0 3 2 1)
            (4 5 6 7)
        );
    }}
    symmetry
    {{
        type symmetryPlane;
        faces
        (
            (0 1 5 4)
        );
    }}
);

mergePatchPairs ();
""")

    def _feature_extract(self):
        _write(self.case, "system/surfaceFeatureExtractDict", """
wing.stl
{
    extractionMethod    extractFromSurface;
    extractFromSurfaceCoeffs
    {
        includedAngle   150;
    }
    subsetFeatures
    {
        nonManifoldEdges    no;
        openEdges           yes;
    }
    writeObj            no;
}
""")

    def _snappy(self):
        of = self.of
        c = self.wing.root_chord
        lo_w, hi_w = self.bbox_wing
        pad = 0.25 * c
        near_min = lo_w - pad
        near_max = hi_w + pad
        near_min[1] = -1.0  # simetri düzleminin ötesine
        wake_min = np.array([lo_w[0] - 0.5 * c, -1.0, lo_w[2] - 0.5 * c])
        wake_max = np.array([hi_w[0] + float(of["wake_length"]) * c, hi_w[1] + 0.5 * c,
                             hi_w[2] + 0.5 * c])
        # iz, serbest akış yönünde (hücum açısıyla) yükselir; kutuyu o yöne genişlet
        rise = math.tan(math.radians(self.alpha)) * float(of["wake_length"]) * c
        wake_min[2] += min(rise, 0.0)
        wake_max[2] += max(rise, 0.0)
        lo, hi = self.domain()
        # locationInMesh: kanadın önünde, hücre yüzlerine denk gelmeyen bir nokta
        loc = np.array([lo[0] + 0.5137 * (0.0 - lo[0]),
                        0.5 * (hi[1] - lo[1]) + 0.0123 * c,
                        lo[2] + 0.4871 * (hi[2] - lo[2])])
        sl = of["surface_level"]
        L = of["layers"]
        add_layers = "true" if L.get("enabled") else "false"
        _write(self.case, "system/snappyHexMeshDict", f"""
castellatedMesh true;
snap            true;
addLayers       {add_layers};

geometry
{{
    wing.stl
    {{
        type triSurfaceMesh;
        name wing;
    }}
    nearBox
    {{
        type searchableBox;
        min {_vec(near_min)};
        max {_vec(near_max)};
    }}
    wakeBox
    {{
        type searchableBox;
        min {_vec(wake_min)};
        max {_vec(wake_max)};
    }}
}}

castellatedMeshControls
{{
    maxLocalCells       5000000;
    maxGlobalCells      40000000;
    minRefinementCells  10;
    maxLoadUnbalance    0.10;
    nCellsBetweenLevels 3;

    features
    (
        {{
            file "wing.eMesh";
            level {int(of["feature_level"])};
        }}
    );

    refinementSurfaces
    {{
        wing
        {{
            level ({int(sl[0])} {int(sl[1])});
            patchInfo {{ type wall; }}
        }}
    }}

    resolveFeatureAngle 30;

    refinementRegions
    {{
        nearBox
        {{
            mode inside;
            levels ((1E15 {int(of["near_level"])}));
        }}
        wakeBox
        {{
            mode inside;
            levels ((1E15 {int(of["wake_level"])}));
        }}
    }}

    locationInMesh {_vec(loc)};
    allowFreeStandingZoneFaces true;
}}

snapControls
{{
    nSmoothPatch    3;
    tolerance       2.0;
    nSolveIter      50;
    nRelaxIter      5;
    nFeatureSnapIter 10;
    implicitFeatureSnap false;
    explicitFeatureSnap true;
    multiRegionFeatureSnap false;
}}

addLayersControls
{{
    relativeSizes   true;
    layers
    {{
        wing
        {{
            nSurfaceLayers {int(L.get("n", 3))};
        }}
    }}
    expansionRatio      {float(L.get("expansion", 1.2))};
    finalLayerThickness {float(L.get("final_thickness", 0.4))};
    minThickness        0.05;
    nGrow               0;
    featureAngle        120;
    slipFeatureAngle    30;
    nRelaxIter          3;
    nSmoothSurfaceNormals 1;
    nSmoothNormals      3;
    nSmoothThickness    10;
    maxFaceThicknessRatio 0.5;
    maxThicknessToMedialRatio 0.3;
    minMedialAxisAngle  90;
    nBufferCellsNoExtrude 0;
    nLayerIter          50;
}}

meshQualityControls
{{
    maxNonOrtho         65;
    maxBoundarySkewness 20;
    maxInternalSkewness 4;
    maxConcave          80;
    minVol              1e-13;
    minTetQuality       1e-15;
    minArea             -1;
    minTwist            0.02;
    minDeterminant      0.001;
    minFaceWeight       0.05;
    minVolRatio         0.01;
    minTriangleTwist    -1;
    nSmoothScale        4;
    errorReduction      0.75;
    relaxed
    {{
        maxNonOrtho     75;
    }}
}}

mergeTolerance 1e-6;
""")

    def _control_dict(self):
        of = self.of
        drag_dir, lift_dir = flow_vectors(self.alpha)
        cofr = np.array([self.wing.mac_x_le + 0.25 * self.wing.mac, 0.0, 0.0])
        body = f"""
application     simpleFoam;
startFrom       latestTime;
startTime       0;
stopAt          endTime;
endTime         {int(of["iterations"])};
deltaT          1;
writeControl    timeStep;
writeInterval   {int(of["write_interval"])};
purgeWrite      2;
writeFormat     ascii;
writePrecision  8;
writeCompression off;
timeFormat      general;
timePrecision   6;
runTimeModifiable true;

functions
{{
    forces
    {{
        type            forces;
        libs            ("libforces.so");
        writeControl    timeStep;
        writeInterval   1;
        log             false;
        patches         (wing);
        rho             rhoInf;
        rhoInf          {self.rho};
        CofR            {_vec(cofr)};
    }}

    forceCoeffs
    {{
        type            forceCoeffs;
        libs            ("libforces.so");
        writeControl    timeStep;
        writeInterval   1;
        log             true;
        patches         (wing);
        rho             rhoInf;
        rhoInf          {self.rho};
        liftDir         {_vec(lift_dir)};
        dragDir         {_vec(drag_dir)};
        CofR            {_vec(cofr)};
        pitchAxis       (0 1 0);
        magUInf         {self.U};
        lRef            {self.wing.mac:.8g};
        Aref            {0.5 * self.wing.area:.8g};
    }}

    yPlus
    {{
        type            yPlus;
        libs            ("libfieldFunctionObjects.so");
        writeControl    writeTime;
    }}

    residuals
    {{
        type            solverInfo;
        libs            ("libutilityFunctionObjects.so");
        fields          (U p k omega);
    }}
}}
"""
        if not use_function_objects(self.cfg):
            # kuvvetler çözüm sonunda Python tarafında alanlardan integre edilir
            body = body[: body.index("functions")] + "functions\n{\n}\n"
        _write(self.case, "system/controlDict", body)

    def _schemes_solution(self):
        _write(self.case, "system/fvSchemes", """
ddtSchemes
{
    default         steadyState;
}

gradSchemes
{
    default         Gauss linear;
    grad(U)         cellLimited Gauss linear 1;
    grad(k)         cellLimited Gauss linear 1;
    grad(omega)     cellLimited Gauss linear 1;
}

divSchemes
{
    default         none;
    div(phi,U)      bounded Gauss linearUpwind grad(U);
    div(phi,k)      bounded Gauss upwind;
    div(phi,omega)  bounded Gauss upwind;
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}

laplacianSchemes
{
    default         Gauss linear limited corrected 0.33;
}

interpolationSchemes
{
    default         linear;
}

snGradSchemes
{
    default         limited corrected 0.33;
}

wallDist
{
    method          meshWave;
}
""")
        _write(self.case, "system/fvSolution", """
solvers
{
    p
    {
        solver          GAMG;
        tolerance       1e-7;
        relTol          0.05;
        smoother        GaussSeidel;
    }

    "(U|k|omega)"
    {
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-8;
        relTol          0.1;
        nSweeps         1;
    }
}

SIMPLE
{
    nNonOrthogonalCorrectors 1;
    consistent      yes;

    residualControl
    {
        p               1e-5;
        U               1e-6;
        "(k|omega)"     1e-6;
    }
}

relaxationFactors
{
    equations
    {
        U               0.7;
        "(k|omega)"     0.5;
    }
}

cache
{
    grad(U);
}
""")

    def _constant(self):
        _write(self.case, "constant/transportProperties", f"""
transportModel  Newtonian;
nu              {self.nu:.8g};
""")
        model = self.of["turbulence_model"]
        _write(self.case, "constant/turbulenceProperties", f"""
simulationType  RAS;

RAS
{{
    RASModel        {model};
    turbulence      on;
    printCoeffs     on;
}}
""")
        # OpenFOAM Foundation (v8+) ve yeni ESI sürümleri için aynı içerik
        _write(self.case, "constant/momentumTransport", f"""
simulationType  RAS;

RAS
{{
    model           {model};
    turbulence      on;
    printCoeffs     on;
}}
""")

    def _initial_fields(self):
        drag_dir, _ = flow_vectors(self.alpha)
        uvec = _vec(self.U * drag_dir)
        k, omega = self.turbulence_inflow()
        nut = k / omega
        common_sym = "    symmetry\n    {\n        type            symmetryPlane;\n    }\n"
        _write(self.case, "0.orig/U", f"""
dimensions      [0 1 -1 0 0 0 0];

internalField   uniform {uvec};

boundaryField
{{
    #includeEtc "caseDicts/setConstraintTypes"

    farfield
    {{
        type            freestreamVelocity;
        freestreamValue uniform {uvec};
        value           uniform {uvec};
    }}
    wing
    {{
        type            noSlip;
    }}
{common_sym}}}
""", cls="volVectorField")
        _write(self.case, "0.orig/p", f"""
dimensions      [0 2 -2 0 0 0 0];

internalField   uniform 0;

boundaryField
{{
    #includeEtc "caseDicts/setConstraintTypes"

    farfield
    {{
        type            freestreamPressure;
        freestreamValue uniform 0;
        value           uniform 0;
    }}
    wing
    {{
        type            zeroGradient;
    }}
{common_sym}}}
""", cls="volScalarField")
        for name, val, dim, wall in (
            ("k", k, "[0 2 -2 0 0 0 0]", f"kqRWallFunction;\n        value           uniform {k:.8g}"),
            ("omega", omega, "[0 0 -1 0 0 0 0]", f"omegaWallFunction;\n        value           uniform {omega:.8g}"),
            ("nut", nut, "[0 2 -1 0 0 0 0]", "nutUSpaldingWallFunction;\n        value           uniform 0"),
        ):
            far = (f"calculated;\n        value           uniform {val:.8g}" if name == "nut" else
                   f"inletOutlet;\n        inletValue      uniform {val:.8g};\n        value           uniform {val:.8g}")
            _write(self.case, f"0.orig/{name}", f"""
dimensions      {dim};

internalField   uniform {val:.8g};

boundaryField
{{
    #includeEtc "caseDicts/setConstraintTypes"

    farfield
    {{
        type            {far};
    }}
    wing
    {{
        type            {wall};
    }}
{common_sym}}}
""", cls="volScalarField")

    def _decompose(self):
        n = max(int(self.of.get("n_procs", 1)), 1)
        # hierarchical her OpenFOAM derlemesinde mevcut (scotch opsiyonel kütüphane)
        split = [1, 1, 1]
        rem, f, axis = n, 2, 0
        while rem > 1:
            while rem % f:
                f += 1
            split[axis % 3] *= f
            rem //= f
            axis += 1
        split.sort(reverse=True)  # en çok bölmeyi akış yönüne (x) ver
        _write(self.case, "system/decomposeParDict", f"""
numberOfSubdomains {n};
method          hierarchical;

hierarchicalCoeffs
{{
    n               ({split[0]} {split[2]} {split[1]});
    delta           0.001;
    order           xyz;
}}
""")

    def _allrun(self):
        n = max(int(self.of.get("n_procs", 1)), 1)
        par = n > 1
        lines = [
            "#!/bin/bash",
            "# Delta kanat 3B RANS vakası - otomatik üretildi",
            "set -e",
            'cd "$(dirname "$0")"',
            "blockMesh > log.blockMesh 2>&1",
            "surfaceFeatureExtract > log.surfaceFeatureExtract 2>&1",
        ]
        if par:
            lines += [
                "decomposePar -force > log.decomposePar.mesh 2>&1",
                f"mpirun -np {n} snappyHexMesh -overwrite -parallel > log.snappyHexMesh 2>&1",
                "for d in processor*; do rm -rf $d/0; cp -r 0.orig $d/0; done",
                f"mpirun -np {n} checkMesh -parallel > log.checkMesh 2>&1 || true",
                f"mpirun -np {n} simpleFoam -parallel > log.simpleFoam 2>&1",
                "reconstructParMesh -constant > log.reconstructParMesh 2>&1 || true",
                "reconstructPar -latestTime > log.reconstructPar 2>&1 || true",
            ]
        else:
            lines += [
                "snappyHexMesh -overwrite > log.snappyHexMesh 2>&1",
                "rm -rf 0 && cp -r 0.orig 0",
                "checkMesh > log.checkMesh 2>&1 || true",
                "simpleFoam > log.simpleFoam 2>&1",
            ]
        lines.append("touch case.foam")
        p = self.case / "Allrun"
        p.write_text("\n".join(lines) + "\n")
        p.chmod(0o755)
        c = self.case / "Allclean"
        c.write_text("#!/bin/bash\ncd \"$(dirname \"$0\")\"\nrm -rf 0 processor* postProcessing log.* [1-9]* constant/polyMesh constant/extendedFeatureEdgeMesh constant/triSurface/*.eMesh\n")
        c.chmod(0o755)

    # --------------------------------------------------------------- run
    def run(self, timeout: float | None = None) -> None:
        bashrc = find_bashrc(self.cfg)
        cmd = f"source {bashrc} > /dev/null 2>&1; ./Allrun" if bashrc else "./Allrun"
        env = dict(os.environ)
        env.setdefault("OMPI_ALLOW_RUN_AS_ROOT", "1")
        env.setdefault("OMPI_ALLOW_RUN_AS_ROOT_CONFIRM", "1")
        proc = subprocess.run(["bash", "-c", cmd], cwd=self.case, env=env, timeout=timeout,
                              capture_output=True, text=True)
        if proc.returncode != 0:
            logs = sorted(self.case.glob("log.*"), key=lambda p: p.stat().st_mtime)
            tail = logs[-1].read_text()[-3000:] if logs else proc.stderr
            raise RuntimeError(f"OpenFOAM çalıştırması başarısız ({self.case}):\n{tail}")

    # --------------------------------------------------------------- results
    def results(self) -> dict:
        return read_results(self.case, int(self.of.get("average_last", 100)))


def use_function_objects(cfg: dict) -> bool:
    """Ubuntu/Debian 'openfoam' (1912) paketinde tüm fonksiyon nesneleri
    'error in IOstream "sha1"' hatasıyla çöker; orada otomatik kapatılır."""
    mode = str(cfg["openfoam"].get("function_objects", "auto")).lower()
    if mode in ("true", "on", "1", "yes"):
        return True
    if mode in ("false", "off", "0", "no"):
        return False
    return find_bashrc(cfg) != "/usr/share/openfoam/etc/bashrc"


# ------------------------------------------------------------------- parsing


def _read_dat(path: Path):
    names, rows = None, []
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            toks = s.lstrip("#").split()
            if toks and toks[0] == "Time":
                names = toks
            continue
        vals = re.sub(r"[()]", " ", s).split()
        try:
            rows.append([float(v) for v in vals])
        except ValueError:
            continue
    if not rows:
        raise RuntimeError(f"Veri yok: {path}")
    width = min(len(r) for r in rows)
    return names, np.array([r[:width] for r in rows])


def read_results(case: str | Path, average_last: int = 100) -> dict:
    """forceCoeffs çıktısından tam kanat CL, CD, kaldırma ve sürükleme kuvvetleri."""
    case = Path(case)
    info = json.loads((case / "case_info.json").read_text())
    files = sorted((case / "postProcessing").glob("forceCoeffs*/*/*.dat"))
    files = [f for f in files if f.name in ("coefficient.dat", "forceCoeffs.dat")] or files
    if not files:
        return results_from_fields(case)
    names, data = _read_dat(files[-1])
    # birden çok restart klasörü varsa birleştir
    for f in files[:-1]:
        _, d = _read_dat(f)
        data = np.vstack([d[:, : data.shape[1]], data])
    names = names or ["Time", "Cm", "Cd", "Cl"]
    idx = {n: i for i, n in enumerate(names)}
    tail = data[-min(average_last, len(data)):]
    cd = float(tail[:, idx["Cd"]].mean())
    cl = float(tail[:, idx["Cl"]].mean())
    q = 0.5 * info["density"] * info["velocity"] ** 2
    S = info["Aref_full_m2"]  # yarı model Aref ile normalize -> katsayılar tam kanat için geçerli
    res = {
        "alpha_deg": info["alpha_deg"],
        "CL": cl,
        "CD": cd,
        "lift_N": cl * q * S,
        "drag_N": cd * q * S,
        "L_over_D": cl / cd if cd != 0 else float("nan"),
        "q_Pa": q,
        "area_m2": S,
        "iterations": int(data[-1, 0]),
        "CL_std_last": float(tail[:, idx["Cl"]].std()),
        "CD_std_last": float(tail[:, idx["Cd"]].std()),
        "method": "openfoam-rans",
    }
    # pressure / viscous ayrımı (forces çıktısından)
    ff = sorted((case / "postProcessing").glob("forces*/*/force.dat"))
    if ff:
        try:
            fn, fd = _read_dat(ff[-1])
            drag_dir, lift_dir = flow_vectors(info["alpha_deg"])
            t = fd[-min(average_last, len(fd)):].mean(axis=0)
            # sütunlar: Time total(3) pressure(3) viscous(3)
            fp, fv = t[4:7], t[7:10]
            res["drag_pressure_N"] = 2 * float(fp @ drag_dir)
            res["drag_viscous_N"] = 2 * float(fv @ drag_dir)
            res["lift_pressure_N"] = 2 * float(fp @ lift_dir)
            res["lift_viscous_N"] = 2 * float(fv @ lift_dir)
        except Exception:  # noqa: BLE001 - isteğe bağlı bilgi
            pass
    return res


def results_from_fields(case: str | Path) -> dict:
    """Son zaman adımındaki p, U, nut alanlarından kuvvetleri integre eder."""
    from .foamforces import integrate_forces

    case = Path(case)
    info = json.loads((case / "case_info.json").read_text())
    f = integrate_forces(case, "wing", info["density"], info["kinematic_viscosity"])
    drag_dir, lift_dir = flow_vectors(info["alpha_deg"])
    q = 0.5 * info["density"] * info["velocity"] ** 2
    S = info["Aref_full_m2"]
    lift = 2.0 * float(f["total"] @ lift_dir)   # yarı model -> tam kanat
    drag = 2.0 * float(f["total"] @ drag_dir)
    return {
        "alpha_deg": info["alpha_deg"],
        "CL": lift / (q * S),
        "CD": drag / (q * S),
        "lift_N": lift,
        "drag_N": drag,
        "L_over_D": lift / drag if drag != 0 else float("nan"),
        "q_Pa": q,
        "area_m2": S,
        "iterations": int(f["time"]),
        "drag_pressure_N": 2.0 * float(f["pressure"] @ drag_dir),
        "drag_viscous_N": 2.0 * float(f["viscous"] @ drag_dir),
        "lift_pressure_N": 2.0 * float(f["pressure"] @ lift_dir),
        "lift_viscous_N": 2.0 * float(f["viscous"] @ lift_dir),
        "method": "openfoam-rans (alan integrasyonu)",
    }


def run_openfoam(wing: DeltaWing, cfg: dict, alpha_deg: float, case_dir: str | Path,
                 execute: bool = True, timeout: float | None = None) -> dict | None:
    case = OpenFOAMCase(wing, cfg, alpha_deg, case_dir)
    case.write()
    if not execute:
        return None
    case.run(timeout=timeout)
    return case.results()


BASHRC_CANDIDATES = (
    "/usr/share/openfoam/etc/bashrc",           # Ubuntu/Debian 'openfoam' paketi
    "/usr/lib/openfoam/openfoam*/etc/bashrc",   # openfoam.com deb/rpm paketleri
    "/opt/openfoam*/etc/bashrc",                # openfoam.org paketleri
    "~/OpenFOAM/OpenFOAM-*/etc/bashrc",         # kaynaktan derleme
)


def find_bashrc(cfg: dict) -> str | None:
    """OpenFOAM ortam dosyası; ortam zaten yüklüyse (WM_PROJECT_DIR) None."""
    if cfg["openfoam"].get("bashrc"):
        return str(Path(cfg["openfoam"]["bashrc"]).expanduser())
    if os.environ.get("WM_PROJECT_DIR"):
        return None
    import glob
    for pat in BASHRC_CANDIDATES:
        hits = sorted(glob.glob(os.path.expanduser(pat)))
        if hits:
            return hits[-1]
    return None


def openfoam_available(cfg: dict) -> bool:
    rc = find_bashrc(cfg)
    if rc:
        return Path(rc).exists()
    return shutil.which("simpleFoam") is not None
