# ControleQualite_py
# Vector QC - Controle qualite de donnees vectorielles

Script Python pour auditer un dossier de donnees SIG vectorielles (Shapefile, GeoJSON, GPKG, KML, FGB) et produire un rapport HTML de qualite.

## 1) Objectif

`vector_qc.py` scanne un dossier, controle la qualite des jeux de donnees vectoriels, puis genere un rapport HTML lisible.

Le script est utile pour:
- verifier rapidement qu'un lot de couches est exploitable
- detecter les erreurs de structure (ex: shapefile incomplet)
- suivre des warnings de qualite (CRS, geometries, attributs)
- partager un diagnostic propre dans un projet GitHub

## 2) Ce que le script controle

### A. Inventaire et format
- scan recursif des sous-dossiers
- exclusion de dossiers (`.git,tmp,backup,__pycache__` par defaut)
- detection des formats supportes: `.shp`, `.geojson/.json`, `.gpkg`, `.kml`, `.fgb`
- controle des composants Shapefile obligatoires: `.shp`, `.shx`, `.dbf`
- warnings sur fichiers optionnels manquants (`.prj`, `.cpg`)

### B. Controle couche (si dependances SIG installees)
Avec `fiona` + `shapely`, le script calcule par couche:
- nombre d'entites
- CRS present/absent + extraction EPSG si possible
- emprise (bbox)
- geometries valides / invalides
- geometries nulles / vides
- types geometriques observes (homogeneite/melange)
- champs avec fort taux de null

### C. Scoring et statut
Chaque couche recoit:
- un statut: `OK`, `WARN`, `ERROR`
- un score de qualite sur 100

## 3) Prerequis

- Python 3.10+
- recommende pour Windows SIG: environnement conda (Miniforge)

Dependances:
- minimales: aucune (mode degrade possible)
- completes: `fiona`, `shapely`

## 4) Installation

### Option recommandee (Miniforge / conda)
```powershell
conda create -n geoqc python=3.11 -y
conda activate geoqc
conda install -c conda-forge fiona shapely -y
```

### Option pip (si wheels disponibles)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install fiona shapely
```

## 5) Utilisation

Depuis le dossier contenant `vector_qc.py`:

```powershell
python .\vector_qc.py --path . --output .\vector_qc_report.html
```

Analyse d'un autre dossier:

```powershell
python .\vector_qc.py --path "D:\Data\Projet_SIG" --output "D:\Data\Projet_SIG\qc_report.html"
```

Options principales:
- `--path` : dossier a analyser
- `--output` : chemin du rapport HTML
- `--exclude` : dossiers a ignorer (csv, ex: `.git,tmp,backup`)
- `--max-invalid-pct` : seuil d'invalidite geometrique avant `ERROR`
- `--fail-on-error` : retourne un code non nul si des erreurs existent

Exemple CI:
```powershell
python .\vector_qc.py --path . --output .\vector_qc_report.html --max-invalid-pct 0 --fail-on-error
```

## 6) Comprendre la chaine de traitement (pipeline)

Le flux interne suit cette sequence:

1. lecture des arguments CLI
2. scan recursif + detection des datasets
3. verification structurelle shapefile (fichiers manquants/orphelins)
4. chargement optionnel des dependances SIG (`fiona`, `shapely`)
5. analyse couche par couche
6. calcul du score + statut
7. aggregation des resultats globaux
8. rendu HTML + ecriture du rapport

### Mode degrade (sans libs SIG)
Si `fiona`/`shapely` ne sont pas installees:
- le script continue
- il fait surtout les controles de structure/format
- le rapport HTML mentionne explicitement les controles non executes

## 7) Lire le rapport HTML

Le rapport contient:
- un bloc resume (`Couches`, `OK`, `WARN`, `ERROR`, `Entites`, `Score moyen`)
- les warnings globaux du scan
- l'etat des dependances
- un tableau detaille par couche avec:
  - fichier/couche/format
  - taille/date
  - entites
  - CRS
  - types geometriques
  - compteurs valides/invalides/null/empty
  - bbox
  - score
  - statut + details (warnings/errors)

## 8) Structure du code (lecture rapide)

Fichier principal: `vector_qc.py`

Fonctions clefs:
- `parse_args()` : configuration CLI
- `scan_datasets()` : inventaire des donnees
- `load_optional_deps()` : detection des libs SIG
- `evaluate_dataset()` : controles detailes par couche
- `evaluate_dataset_without_geo_libs()` : fallback sans libs
- `compute_score()` : calcul score qualite
- `summarize_results()` : resume global
- `render_html_report()` : generation HTML
- `run_qc()` : orchestration complete

## 9) Limites connues (v1)

- pas encore de validation topologique avancee (overlap, network connectivity)
- pas de regles metier YAML/JSON (champs obligatoires, domaines autorises)
- pas de correction automatique (`make_valid`, reprojection)

## 10) Publication GitHub conseillee

Ajouter au depot:
- `vector_qc.py`
- `README.md` (ce fichier)
- eventuellement `requirements.txt` ou `environment.yml`
- un exemple de rapport HTML (optionnel)

Exemple minimal `requirements.txt`:
```txt
fiona
shapely
```

## 11) Exemple de commande finale

```powershell
python .\vector_qc.py --path "C:\Users\...\Topo1M_Senegal_shp" --output ".\vector_qc_report.html"
```

---

Si tu veux, la prochaine etape logique est d'ajouter:
- un `requirements.txt` fige (`pip freeze` cible)
- un `environment.yml` conda
- un workflow GitHub Actions qui lance `vector_qc.py` sur un jeu de test
