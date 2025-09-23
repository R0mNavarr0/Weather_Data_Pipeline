# --------- Importation des packages ----------
import os, io, gzip, boto3, pandas as pd, numpy as np, json
from dotenv import load_dotenv

# --------- Chargement des variables d'environnement ----------

load_dotenv()
AWS_REGION = os.getenv("AWS_REGION")
S3_BUCKET  = os.getenv("S3_BUCKET_STAGING")
S3_BUCKET_RDY = os.getenv("S3_BUCKET_RDY")
OUT_KEY = os.getenv("OUT_KEY")

# --------- Connexion au bucket S3 ----------

s3 = boto3.client("s3", region_name=AWS_REGION)

# --------- Définitions des fonctions ----------

# Fonction pour récupérer les clés dans le bucket S3
def iter_s3_keys(bucket, suffix=None, prefix=None):
    token = None
    while True:
        kw = {"Bucket": bucket}
        if token: kw["ContinuationToken"] = token
        if prefix: kw["Prefix"] = prefix
        resp = s3.list_objects_v2(**kw)
        for c in resp.get("Contents", []):
            k = c["Key"]
            if not suffix or k.endswith(suffix):
                yield k
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")

# Fonction pour charger les fichiers dans un dataframe
def read_jsonl_gz_from_s3(bucket, key):
    obj = s3.get_object(Bucket=bucket, Key=key)
    with gzip.GzipFile(fileobj=obj["Body"]) as gz:
        # pd.read_json accepte un file-like texte
        df = pd.read_json(io.TextIOWrapper(gz, encoding="utf-8"), lines=True)
    return df

# Fonction de nettoyage des données
def clean_numeric(series, unit=None, factor=1.0, decimals=None):
    s = series.astype(str).str.replace("\xa0"," ", regex=False).str.strip()
    if unit: s = s.str.replace(unit, "", regex=False).str.strip()
    s = pd.to_numeric(s, errors="coerce") * factor
    if decimals is not None: s = s.round(decimals)
    return s

# --------- Chargement du fichier infoclimat depuis S3 ----------
key_info = max([k for k in iter_s3_keys(S3_BUCKET, suffix=".jsonl.gz", prefix="greenandcoop-staging/infoclimat/")])
df_raw_infoclimat = read_jsonl_gz_from_s3(S3_BUCKET, key_info)

raw = df_raw_infoclimat["_airbyte_data"].iloc[0]
hourly = raw["hourly"]
stations = pd.DataFrame(raw["stations"])

# Aplatir hourly (une ligne = un relevé)
dfs = []
for sid, recs in hourly.items():
    if sid == "_params": 
        continue
    df = pd.DataFrame.from_records(recs)
    df["id_station"] = sid
    dfs.append(df)
df_infoclimat = pd.concat(dfs, ignore_index=True)

# --------- Transformation du Dataframe infoclimat ----------

# Joindre métadonnées stations
stations = stations.rename(columns={"id":"id_station", "name":"station_name"})
df_infoclimat = df_infoclimat.merge(stations, on="id_station", how="left")

# Types et découpes temporelles
df_infoclimat = df_infoclimat.rename(columns={"id_station":"station_id"})
for c in ["latitude","longitude","elevation"]:
    df_infoclimat[c] = pd.to_numeric(df_infoclimat[c], errors="coerce")

df_infoclimat["dh_utc"] = pd.to_datetime(df_infoclimat["dh_utc"], utc=True, errors="coerce")
df_infoclimat["date"] = df_infoclimat["dh_utc"].dt.tz_convert("Europe/Paris").dt.normalize()
df_infoclimat["time"] = df_infoclimat["dh_utc"].dt.tz_convert("Europe/Paris").dt.strftime("%H:%M:%S")
df_infoclimat = df_infoclimat.drop(columns=["dh_utc"])

num_cols_info = ["temperature","point_de_rosee","visibilite","humidite",
                 "vent_direction","vent_moyen","vent_rafales","pression",
                 "pluie_1h","pluie_3h","neige_au_sol","nebulosite"]
for c in num_cols_info:
    if c in df_infoclimat:
        df_infoclimat[c] = pd.to_numeric(df_infoclimat[c], errors="coerce")

# --------- Chargement des fichiers CSV depuis S3 ----------
key_csv = max([k for k in iter_s3_keys(S3_BUCKET, suffix=".jsonl.gz", prefix="greenandcoop-staging/greenandcoop-csvfiles/")])
df_csv_raw = read_jsonl_gz_from_s3(S3_BUCKET, key_csv)
df_csv = pd.DataFrame(df_csv_raw["_airbyte_data"].to_dict()).T.reset_index(drop=True)
df_csv.columns = df_csv.columns.str.lower()

# --------- Transformation du Dataframe CSV ----------
rename_map = {
    "dew point":"point_de_rosee","humidity":"humidite","wind":"vent_direction",
    "speed":"vent_moyen","gust":"vent_rafales","pressure":"pression",
    "precip. rate.":"precipitation_moyenne","precip. accum.":"precipitation_acc",
    "solar":"flux_solaire"
}
df_csv = df_csv.rename(columns=rename_map)

# Conversions CSV
for c in ["latitude","longitude","elevation","uv"]:
    if c in df_csv: df_csv[c] = pd.to_numeric(df_csv[c], errors="coerce")
df_csv["date"] = pd.to_datetime(df_csv["date"], format="%d%m%y", errors="coerce")
df_csv["date"] = df_csv["date"].dt.tz_localize("Europe/Paris")
df_csv["time"] = pd.to_datetime(df_csv["time"], format="%H:%M:%S", errors="coerce").dt.strftime("%H:%M:%S")

# °F -> °C
df_csv["temperature"]     = (clean_numeric(df_csv["temperature"], unit="°F")     - 32)*5/9
df_csv["point_de_rosee"]  = (clean_numeric(df_csv["point_de_rosee"], unit="°F") - 32)*5/9
df_csv[["temperature","point_de_rosee"]] = df_csv[["temperature","point_de_rosee"]].round(1)

# % / vent / pression / pluie / flux
df_csv["humidite"]              = clean_numeric(df_csv["humidite"], unit="%", decimals=0)
wind_map = {"N":0,"North":0,"NNE":22.5,"NE":45,"ENE":67.5,"E":90,"East":90,"ESE":112.5,
            "SE":135,"SSE":157.5,"S":180,"South":180,"SSW":202.5,"SW":225,"WSW":247.5,
            "W":270,"West":270,"WNW":292.5,"NW":315,"NNW":337.5}
df_csv["vent_direction"] = df_csv["vent_direction"].map(wind_map)
df_csv["vent_moyen"]     = clean_numeric(df_csv["vent_moyen"],  unit="mph", factor=1.60934, decimals=1)
df_csv["vent_rafales"]   = clean_numeric(df_csv["vent_rafales"],unit="mph", factor=1.60934, decimals=1)
df_csv["pression"]       = clean_numeric(df_csv["pression"],     unit="in",  factor=33.8639, decimals=1)
df_csv["precipitation_moyenne"] = clean_numeric(df_csv["precipitation_moyenne"], unit="in", factor=25.4, decimals=1)
df_csv["precipitation_acc"]     = clean_numeric(df_csv["precipitation_acc"],     unit="in", factor=25.4, decimals=1)
df_csv["flux_solaire"]   = clean_numeric(df_csv["flux_solaire"], unit="w/m²")

# --------- Concaténation des Dataframes ----------
schema = [
    "station_id","station_name","city","latitude","longitude","elevation","software",
    "date","time","type","license","temperature","point_de_rosee","visibilite","humidite",
    "vent_direction","vent_moyen","vent_rafales","pression","pluie_1h","pluie_3h",
    "precipitation_moyenne","precipitation_acc","uv","flux_solaire","neige_au_sol",
    "nebulosite","temps_omm"
]

# Harmoniser les deux DF au schéma cible
df_infoclimat = df_infoclimat.reindex(columns=schema)
df_csv        = df_csv.reindex(columns=schema)

df_final = pd.concat([df_infoclimat, df_csv], ignore_index=True)
df_final = df_final.replace({None:np.nan})

# --------- Préparation des données pour MongoDB ----------

# 2. Conversion pandas NaN / NaT en None pour compatibilité Mongo
def to_mongo_records(df: pd.DataFrame):
    for rec in df.to_dict("records"):
        for k, v in rec.items():
            if pd.isna(v):
                rec[k] = None
            elif isinstance(v, (pd.Timestamp, np.datetime64)):
                rec[k] = str(v)
        yield rec

mongo_ready = list(to_mongo_records(df_final))

with io.BytesIO() as buffer:
    for rec in mongo_ready:
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        buffer.write(line.encode("utf-8"))
    buffer.seek(0)
    s3.upload_fileobj(buffer, S3_BUCKET_RDY, OUT_KEY)

print(f"✅ Données prêtes pour MongoDB sauvegardées dans s3://{S3_BUCKET_RDY}/{OUT_KEY}")