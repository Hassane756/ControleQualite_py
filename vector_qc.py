#!/usr/bin/env python3
"""Controle qualite vectoriel avec export HTML."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


SUPPORTED_SINGLE_FILE_FORMATS: Dict[str, str] = {
    ".geojson": "GeoJSON",
    ".json": "GeoJSON",
    ".gpkg": "GeoPackage",
    ".kml": "KML",
    ".fgb": "FlatGeobuf",
}

SHAPEFILE_REQUIRED_EXTENSIONS = {".shp", ".shx", ".dbf"}
SHAPEFILE_OPTIONAL_EXTENSIONS = {".prj", ".cpg"}
SHAPEFILE_SIDE_CAR_EXTENSIONS = {
    ".sbn",
    ".sbx",
    ".qix",
    ".fix",
    ".xml",
    ".shp.xml",
}


@dataclass
class DatasetEntry:
    path: Path
    format_name: str
    missing_components: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass
class LayerResult:
    dataset_path: Path
    layer_name: str
    format_name: str
    file_size_bytes: int
    modified_time: dt.datetime
    feature_count: Optional[int] = None
    crs_present: bool = False
    crs_text: str = "N/A"
    epsg: Optional[int] = None
    bounds: Optional[Tuple[float, float, float, float]] = None
    geom_valid_count: int = 0
    geom_invalid_count: int = 0
    geom_null_count: int = 0
    geom_empty_count: int = 0
    geometry_types: Set[str] = field(default_factory=set)
    field_count: int = 0
    fields_too_long: List[str] = field(default_factory=list)
    high_null_fields: List[str] = field(default_factory=list)
    checks_executed: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    score: int = 100

    def status(self) -> str:
        if self.errors:
            return "ERROR"
        if self.warnings:
            return "WARN"
        return "OK"

    def invalid_pct(self) -> float:
        inspected = self.geom_valid_count + self.geom_invalid_count
        if inspected == 0:
            return 0.0
        return (self.geom_invalid_count / inspected) * 100.0


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Controle qualite de donnees vectorielles avec rapport HTML."
    )
    parser.add_argument(
        "--path",
        default=".",
        help="Dossier racine a parcourir (recursif).",
    )
    parser.add_argument(
        "--output",
        default="vector_qc_report.html",
        help="Chemin du rapport HTML.",
    )
    parser.add_argument(
        "--exclude",
        default=".git,tmp,backup,__pycache__",
        help="Dossiers a exclure (liste separee par des virgules).",
    )
    parser.add_argument(
        "--max-invalid-pct",
        type=float,
        default=0.0,
        help="Seuil max de geometries invalides (%%) avant ERROR.",
    )
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help="Retourne un code non nul s'il y a au moins une erreur.",
    )
    return parser.parse_args(argv)


def load_optional_deps() -> Tuple[Any, Any, List[str]]:
    missing: List[str] = []
    fiona_mod = None
    shape_fn = None

    try:
        import fiona  # type: ignore

        fiona_mod = fiona
    except ImportError:
        missing.append("fiona")

    try:
        from shapely.geometry import shape  # type: ignore

        shape_fn = shape
    except ImportError:
        missing.append("shapely")

    return fiona_mod, shape_fn, missing


def scan_datasets(root: Path, excluded_dirs: Set[str]) -> Tuple[List[DatasetEntry], List[str]]:
    datasets: List[DatasetEntry] = []
    global_warnings: List[str] = []

    all_files: List[Path] = []
    for current_root, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d.lower() not in excluded_dirs]
        for file_name in files:
            all_files.append(Path(current_root) / file_name)

    shp_groups: Dict[Path, Set[str]] = {}
    for file_path in all_files:
        lower_name = file_path.name.lower()
        suffix = file_path.suffix.lower()
        if lower_name.endswith(".shp.xml"):
            shp_groups.setdefault(file_path.with_suffix("").with_suffix(""), set()).add(".shp.xml")
            continue
        stem_path = file_path.with_suffix("")
        if suffix in SHAPEFILE_REQUIRED_EXTENSIONS or suffix in SHAPEFILE_OPTIONAL_EXTENSIONS:
            shp_groups.setdefault(stem_path, set()).add(suffix)
        elif suffix in SHAPEFILE_SIDE_CAR_EXTENSIONS:
            shp_groups.setdefault(stem_path, set()).add(suffix)
        elif suffix in SUPPORTED_SINGLE_FILE_FORMATS:
            datasets.append(
                DatasetEntry(
                    path=file_path,
                    format_name=SUPPORTED_SINGLE_FILE_FORMATS[suffix],
                )
            )

    shp_stems_with_main = {stem for stem, exts in shp_groups.items() if ".shp" in exts}
    for stem in sorted(shp_groups.keys()):
        exts = shp_groups[stem]
        if ".shp" not in exts:
            if any(ext in SHAPEFILE_REQUIRED_EXTENSIONS for ext in exts):
                global_warnings.append(
                    f"Fichier orphelin detecte pour '{stem.name}' (sans .shp): {', '.join(sorted(exts))}"
                )
            continue

        missing_components = sorted(SHAPEFILE_REQUIRED_EXTENSIONS - exts)
        entry = DatasetEntry(
            path=stem.with_suffix(".shp"),
            format_name="ESRI Shapefile",
            missing_components=missing_components,
        )
        if missing_components:
            entry.errors.append(
                "Shapefile incomplet: composants manquants "
                + ", ".join(missing_components)
            )
        if ".prj" not in exts:
            entry.warnings.append("Fichier .prj manquant (CRS potentiellement absent).")
        if ".cpg" not in exts:
            entry.warnings.append("Fichier .cpg manquant (encodage DBF potentiellement ambigu).")
        datasets.append(entry)

    known_shp_stems = shp_stems_with_main
    for file_path in all_files:
        lower_name = file_path.name.lower()
        if lower_name.endswith(".shp.xml"):
            stem = file_path.with_suffix("").with_suffix("")
            if stem not in known_shp_stems:
                global_warnings.append(f"Metadonnee '.shp.xml' orpheline: {file_path}")
            continue
        suffix = file_path.suffix.lower()
        if suffix in SHAPEFILE_REQUIRED_EXTENSIONS | SHAPEFILE_OPTIONAL_EXTENSIONS | SHAPEFILE_SIDE_CAR_EXTENSIONS:
            stem = file_path.with_suffix("")
            if stem not in known_shp_stems and suffix != ".shp":
                global_warnings.append(f"Composant shapefile orphelin: {file_path}")

    datasets = sorted(datasets, key=lambda d: str(d.path).lower())
    return datasets, sorted(set(global_warnings))


def safe_layer_names(fiona_mod: Any, dataset_path: Path) -> List[str]:
    try:
        layers = fiona_mod.listlayers(dataset_path)
        if layers:
            return list(layers)
    except Exception:
        pass
    return [dataset_path.stem]


def normalize_geom_name(name: str) -> str:
    cleaned = name.strip().lower()
    cleaned = cleaned.replace(" ", "")
    cleaned = cleaned.replace("z", "")
    cleaned = cleaned.replace("m", "")
    return cleaned


def crs_to_text(crs_obj: Any, crs_wkt: Optional[str]) -> Tuple[str, Optional[int]]:
    epsg: Optional[int] = None
    if hasattr(crs_obj, "to_epsg"):
        try:
            epsg = crs_obj.to_epsg()
        except Exception:
            epsg = None
    elif isinstance(crs_obj, dict):
        init = crs_obj.get("init")
        if isinstance(init, str) and init.lower().startswith("epsg:"):
            try:
                epsg = int(init.split(":")[-1])
            except ValueError:
                epsg = None
        code = crs_obj.get("epsg")
        if epsg is None and isinstance(code, int):
            epsg = code

    if epsg is not None:
        return f"EPSG:{epsg}", epsg
    if crs_obj:
        return str(crs_obj), None
    if crs_wkt:
        preview = crs_wkt.strip().replace("\n", " ")
        if len(preview) > 80:
            preview = preview[:77] + "..."
        return preview, None
    return "N/A", None


def evaluate_dataset(
    entry: DatasetEntry,
    fiona_mod: Any,
    shape_fn: Any,
    max_invalid_pct: float,
) -> List[LayerResult]:
    stat = entry.path.stat()
    modified = dt.datetime.fromtimestamp(stat.st_mtime)
    layer_results: List[LayerResult] = []

    layers = safe_layer_names(fiona_mod, entry.path)
    for layer_name in layers:
        result = LayerResult(
            dataset_path=entry.path,
            layer_name=layer_name,
            format_name=entry.format_name,
            file_size_bytes=stat.st_size,
            modified_time=modified,
            warnings=list(entry.warnings),
            errors=list(entry.errors),
        )

        if entry.missing_components:
            result.checks_executed.append("format")
            result.score = compute_score(result)
            layer_results.append(result)
            continue

        open_kwargs: Dict[str, Any] = {}
        if entry.format_name != "ESRI Shapefile":
            open_kwargs["layer"] = layer_name

        try:
            with fiona_mod.open(entry.path, **open_kwargs) as src:
                result.checks_executed.extend(["format", "crs", "geometry", "attributes"])
                result.feature_count = len(src)
                result.bounds = src.bounds
                result.field_count = len(src.schema.get("properties", {}))

                crs_text, epsg = crs_to_text(src.crs, src.crs_wkt)
                result.crs_text = crs_text
                result.epsg = epsg
                result.crs_present = bool(src.crs or src.crs_wkt)
                if not result.crs_present:
                    result.errors.append("CRS absent.")

                if result.epsg == 4326 and result.bounds:
                    minx, miny, maxx, maxy = result.bounds
                    if (
                        minx < -180
                        or maxx > 180
                        or miny < -90
                        or maxy > 90
                    ):
                        result.warnings.append(
                            "Coordonnees suspectes pour EPSG:4326 (hors bornes lon/lat)."
                        )

                properties = src.schema.get("properties", {})
                null_count_by_field = {field_name: 0 for field_name in properties.keys()}
                if entry.format_name == "ESRI Shapefile":
                    for field_name in properties.keys():
                        if len(field_name) > 10:
                            result.fields_too_long.append(field_name)
                    if result.fields_too_long:
                        result.warnings.append(
                            "Noms de champs > 10 caracteres (limite shapefile): "
                            + ", ".join(result.fields_too_long)
                        )

                expected_geom = src.schema.get("geometry") or ""
                expected_norm = normalize_geom_name(expected_geom) if expected_geom else ""

                for feature in src:
                    geometry = feature.get("geometry")
                    attributes = feature.get("properties") or {}
                    for field_name in null_count_by_field:
                        value = attributes.get(field_name)
                        if value is None:
                            null_count_by_field[field_name] += 1
                        elif isinstance(value, str) and value.strip() == "":
                            null_count_by_field[field_name] += 1

                    if geometry is None:
                        result.geom_null_count += 1
                        continue

                    gtype = geometry.get("type")
                    if gtype:
                        result.geometry_types.add(gtype)

                    try:
                        geom_obj = shape_fn(geometry)
                    except Exception as exc:
                        result.geom_invalid_count += 1
                        result.warnings.append(f"Geometrie non lisible: {exc}")
                        continue

                    if geom_obj.is_empty:
                        result.geom_empty_count += 1
                    if geom_obj.is_valid:
                        result.geom_valid_count += 1
                    else:
                        result.geom_invalid_count += 1

                feature_total = result.feature_count or 0
                for field_name, null_count in null_count_by_field.items():
                    if feature_total == 0:
                        continue
                    pct = (null_count / feature_total) * 100.0
                    if pct >= 90.0:
                        result.high_null_fields.append(f"{field_name} ({pct:.1f}% null)")
                if result.high_null_fields:
                    result.warnings.append(
                        "Champs majoritairement null: " + ", ".join(result.high_null_fields)
                    )

                actual_norm_types = {
                    normalize_geom_name(name) for name in result.geometry_types if name
                }
                if len(actual_norm_types) > 1:
                    result.warnings.append(
                        "Types geometriques melanges: "
                        + ", ".join(sorted(result.geometry_types))
                    )
                if expected_norm and actual_norm_types:
                    compatible = set()
                    compatible.add(expected_norm)
                    if expected_norm.startswith("multi"):
                        compatible.add(expected_norm[5:])
                    else:
                        compatible.add("multi" + expected_norm)
                    if not actual_norm_types.issubset(compatible):
                        result.warnings.append(
                            "Type geometrie inattendu. Schema='"
                            + expected_geom
                            + "', observe='"
                            + ", ".join(sorted(result.geometry_types))
                            + "'"
                        )

                if result.invalid_pct() > max_invalid_pct:
                    result.errors.append(
                        f"Taux de geometries invalides {result.invalid_pct():.2f}% "
                        f"> seuil {max_invalid_pct:.2f}%."
                    )
        except Exception as exc:
            result.errors.append(f"Impossible d'ouvrir la couche: {exc}")

        result.score = compute_score(result)
        layer_results.append(result)

    return layer_results


def evaluate_dataset_without_geo_libs(entry: DatasetEntry) -> List[LayerResult]:
    stat = entry.path.stat()
    modified = dt.datetime.fromtimestamp(stat.st_mtime)
    result = LayerResult(
        dataset_path=entry.path,
        layer_name=entry.path.stem,
        format_name=entry.format_name,
        file_size_bytes=stat.st_size,
        modified_time=modified,
        warnings=list(entry.warnings),
        errors=list(entry.errors),
    )
    result.checks_executed = ["format"]
    result.warnings.append(
        "Controles CRS/geometrie/attributs non executes (dependances manquantes)."
    )
    result.score = compute_score(result)
    return [result]


def compute_score(result: LayerResult) -> int:
    penalty = 0
    penalty += len(result.errors) * 25
    penalty += len(result.warnings) * 8
    penalty += int(round(result.invalid_pct()))
    score = 100 - penalty
    if score < 0:
        return 0
    if score > 100:
        return 100
    return score


def summarize_results(results: Iterable[LayerResult]) -> Dict[str, Any]:
    rows = list(results)
    return {
        "layers": len(rows),
        "errors": sum(1 for r in rows if r.errors),
        "warnings": sum(1 for r in rows if (not r.errors and r.warnings)),
        "ok": sum(1 for r in rows if not r.errors and not r.warnings),
        "features": sum(r.feature_count or 0 for r in rows),
        "avg_score": (
            round(sum(r.score for r in rows) / len(rows), 1) if rows else 0.0
        ),
    }


def fmt_bytes(size_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size_bytes)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{size_bytes} B"


def list_to_html(items: Sequence[str]) -> str:
    if not items:
        return "<span class='muted'>Aucun</span>"
    escaped = "".join(f"<li>{html.escape(item)}</li>" for item in items)
    return f"<ul>{escaped}</ul>"


def render_html_report(
    output_path: Path,
    root_path: Path,
    results: List[LayerResult],
    global_warnings: List[str],
    missing_deps: List[str],
    started_at: dt.datetime,
    duration_seconds: float,
) -> None:
    summary = summarize_results(results)
    generated_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows_html = []

    for result in results:
        details = (
            "<details><summary>Details</summary>"
            f"<p><strong>Checks:</strong> {html.escape(', '.join(result.checks_executed) or 'N/A')}</p>"
            f"<p><strong>Warnings:</strong> {list_to_html(result.warnings)}</p>"
            f"<p><strong>Errors:</strong> {list_to_html(result.errors)}</p>"
            "</details>"
        )
        geom_types = ", ".join(sorted(result.geometry_types)) if result.geometry_types else "N/A"
        feature_count = "N/A" if result.feature_count is None else str(result.feature_count)
        bounds_txt = "N/A"
        if result.bounds:
            minx, miny, maxx, maxy = result.bounds
            bounds_txt = f"[{minx:.4f}, {miny:.4f}, {maxx:.4f}, {maxy:.4f}]"

        row = f"""
        <tr class="{result.status().lower()}">
          <td>{html.escape(str(result.dataset_path))}</td>
          <td>{html.escape(result.layer_name)}</td>
          <td>{html.escape(result.format_name)}</td>
          <td>{fmt_bytes(result.file_size_bytes)}</td>
          <td>{result.modified_time.strftime('%Y-%m-%d %H:%M:%S')}</td>
          <td>{feature_count}</td>
          <td>{html.escape(result.crs_text)}</td>
          <td>{html.escape(geom_types)}</td>
          <td>{result.geom_valid_count}</td>
          <td>{result.geom_invalid_count}</td>
          <td>{result.geom_null_count}</td>
          <td>{result.geom_empty_count}</td>
          <td>{html.escape(bounds_txt)}</td>
          <td>{result.score}</td>
          <td><span class="badge {result.status().lower()}">{result.status()}</span>{details}</td>
        </tr>
        """
        rows_html.append(row)

    warnings_html = list_to_html(global_warnings)
    missing_deps_html = list_to_html(
        [
            "Dependances manquantes: " + ", ".join(missing_deps),
            "Installer avec: pip install fiona shapely",
        ]
        if missing_deps
        else []
    )

    html_content = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Rapport QC Vectoriel</title>
  <style>
    :root {{
      --bg: #f4f7fb;
      --card: #ffffff;
      --ink: #10243e;
      --muted: #6c7a8a;
      --ok: #1a7f37;
      --warn: #b07a00;
      --error: #c11f1f;
      --line: #dce4ef;
      --accent: #1166cc;
    }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      background: radial-gradient(circle at 10% 10%, #e8f0ff 0%, var(--bg) 40%);
      color: var(--ink);
      line-height: 1.4;
    }}
    .container {{
      max-width: 1400px;
      margin: 24px auto;
      padding: 0 20px 40px;
    }}
    .header {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 16px 20px;
      box-shadow: 0 4px 10px rgba(0, 0, 0, 0.04);
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 1.5rem;
    }}
    .meta {{
      color: var(--muted);
      font-size: 0.95rem;
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 12px;
      margin: 16px 0;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 12px;
    }}
    .card .label {{
      color: var(--muted);
      font-size: 0.85rem;
    }}
    .card .value {{
      font-size: 1.2rem;
      font-weight: 700;
      margin-top: 2px;
    }}
    .box {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 12px 14px;
      margin: 10px 0;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 10px;
      overflow: hidden;
      font-size: 0.9rem;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 10px 8px;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      background: #edf3fb;
      position: sticky;
      top: 0;
      z-index: 1;
    }}
    tr.ok td {{
      background: #f2fbf4;
    }}
    tr.warn td {{
      background: #fff9ea;
    }}
    tr.error td {{
      background: #fff0f0;
    }}
    .badge {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      color: #fff;
      font-weight: 600;
      font-size: 0.75rem;
      margin-bottom: 6px;
    }}
    .badge.ok {{ background: var(--ok); }}
    .badge.warn {{ background: var(--warn); }}
    .badge.error {{ background: var(--error); }}
    .muted {{ color: var(--muted); }}
    details summary {{
      cursor: pointer;
      color: var(--accent);
      font-weight: 600;
    }}
    ul {{
      margin: 4px 0 0 18px;
      padding: 0;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>Rapport Controle Qualite Vectoriel</h1>
      <div class="meta">
        <div><strong>Dossier scanne:</strong> {html.escape(str(root_path))}</div>
        <div><strong>Genere le:</strong> {generated_at}</div>
        <div><strong>Duree:</strong> {duration_seconds:.2f} sec</div>
      </div>
    </div>

    <div class="summary">
      <div class="card"><div class="label">Couches</div><div class="value">{summary['layers']}</div></div>
      <div class="card"><div class="label">OK</div><div class="value">{summary['ok']}</div></div>
      <div class="card"><div class="label">WARN</div><div class="value">{summary['warnings']}</div></div>
      <div class="card"><div class="label">ERROR</div><div class="value">{summary['errors']}</div></div>
      <div class="card"><div class="label">Entites totales</div><div class="value">{summary['features']}</div></div>
      <div class="card"><div class="label">Score moyen</div><div class="value">{summary['avg_score']}</div></div>
    </div>

    <div class="box">
      <strong>Warnings globales du scan</strong>
      {warnings_html}
    </div>

    <div class="box">
      <strong>Etat dependances</strong>
      {missing_deps_html if missing_deps else "<span class='muted'>Toutes les dependances SIG sont disponibles.</span>"}
    </div>

    <table>
      <thead>
        <tr>
          <th>Fichier</th>
          <th>Couche</th>
          <th>Format</th>
          <th>Taille</th>
          <th>Modifie le</th>
          <th>Entites</th>
          <th>CRS</th>
          <th>Types geom.</th>
          <th>Valides</th>
          <th>Invalides</th>
          <th>Null</th>
          <th>Empty</th>
          <th>BBOX</th>
          <th>Score</th>
          <th>Statut</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows_html)}
      </tbody>
    </table>
  </div>
</body>
</html>
"""

    output_path.write_text(html_content, encoding="utf-8")


def run_qc(args: argparse.Namespace) -> int:
    started_clock = time.time()
    started_at = dt.datetime.now()

    root = Path(args.path).resolve()
    if not root.exists():
        print(f"[ERROR] Le dossier n'existe pas: {root}")
        return 2

    excluded_dirs = {
        item.strip().lower() for item in args.exclude.split(",") if item.strip()
    }
    datasets, global_warnings = scan_datasets(root, excluded_dirs)
    if not datasets:
        print("[WARN] Aucun dataset vectoriel detecte.")

    fiona_mod, shape_fn, missing_deps = load_optional_deps()
    results: List[LayerResult] = []

    for index, entry in enumerate(datasets, start=1):
        print(f"[{index}/{len(datasets)}] Analyse: {entry.path.name}")
        if fiona_mod is not None and shape_fn is not None:
            layer_results = evaluate_dataset(
                entry=entry,
                fiona_mod=fiona_mod,
                shape_fn=shape_fn,
                max_invalid_pct=args.max_invalid_pct,
            )
        else:
            layer_results = evaluate_dataset_without_geo_libs(entry)
        results.extend(layer_results)

    report_path = Path(args.output).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    render_html_report(
        output_path=report_path,
        root_path=root,
        results=results,
        global_warnings=global_warnings,
        missing_deps=missing_deps,
        started_at=started_at,
        duration_seconds=(time.time() - started_clock),
    )

    summary = summarize_results(results)
    print(
        "[DONE] Rapport HTML genere: "
        f"{report_path} | OK={summary['ok']} WARN={summary['warnings']} ERROR={summary['errors']}"
    )

    if args.fail_on_error and summary["errors"] > 0:
        return 3
    return 0


def main() -> None:
    args = parse_args()
    exit_code = run_qc(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
