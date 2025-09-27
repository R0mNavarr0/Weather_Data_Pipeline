import os, io, boto3, json
import pandas as pd
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

AWS_REGION   = os.getenv("AWS_REGION")
S3_BUCKET_RDY = os.getenv("S3_BUCKET_RDY")
MONGO_URI    = os.getenv("MONGO_URI")
MONGO_DB     = os.getenv("MONGO_DB")
MONGO_COL    = os.getenv("MONGO_COL")

# --- Connexions ---
s3 = boto3.client("s3", region_name=AWS_REGION)
mongo_client = MongoClient(MONGO_URI)
collection = mongo_client[MONGO_DB][MONGO_COL]

# --- Récupération dernier fichier prêt pour Mongo ---
def get_latest_ready_file(bucket, prefix=""):
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    if "Contents" not in resp:
        raise FileNotFoundError("Aucun fichier trouvé dans le bucket.")
    latest = max(resp["Contents"], key=lambda x: x["LastModified"])
    return latest["Key"]

key = get_latest_ready_file(S3_BUCKET_RDY)
print(f"➡️ Fichier source de référence : {key}")

obj = s3.get_object(Bucket=S3_BUCKET_RDY, Key=key)
lines = obj["Body"].read().decode("utf-8").splitlines()
docs_src = [json.loads(l) for l in lines]
df_src = pd.DataFrame(docs_src)

# --- Chargement Mongo ---
docs_mongo = list(collection.find({}, {"_id":0}))
df_mongo = pd.DataFrame(docs_mongo)

# --- Vérification volumes ---
expected = len(df_src)
inserted = len(df_mongo)
row_error_rate = (expected - inserted) / expected if expected > 0 else 0

print(f"Documents attendus : {expected}")
print(f"Documents insérés  : {inserted}")
print(f"Taux d'erreur (lignes perdues) : {row_error_rate:.2%}")

# --- Complétude des colonnes ---
null_rate_src = df_src.isna().mean()
null_rate_mongo = df_mongo.isna().mean()
comparison = pd.DataFrame({
    "src_null_rate": null_rate_src,
    "mongo_null_rate": null_rate_mongo
})
comparison["diff"] = comparison["mongo_null_rate"] - comparison["src_null_rate"]

field_error_rate = comparison["diff"].abs().sum() / (len(df_src.columns)) if expected > 0 else 0
print(f"Taux d'erreur (complétude champs) : {field_error_rate:.2%}")

# --- Vérification valeurs numériques (stats simples) ---
num_cols = [c for c in df_src.columns if pd.api.types.is_numeric_dtype(df_src[c])]
for col in num_cols:
    try:
        src_mean = df_src[col].mean()
        mongo_mean = df_mongo[col].mean()
        if pd.notna(src_mean) and pd.notna(mongo_mean):
            diff = abs(src_mean - mongo_mean)
            print(f"[{col}] Moyenne source={src_mean:.2f}, mongo={mongo_mean:.2f}, diff={diff:.2f}")
    except Exception as e:
        print(f"[{col}] Impossible de comparer ({e})")

# --- Score global ---
total_error_rate = row_error_rate + field_error_rate
print("------")
print(f"Taux d'erreur global estimé : {total_error_rate:.2%}")