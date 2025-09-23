# --------- Importation des packages ----------
import os, json, gzip, io, boto3
import pandas as pd
from pymongo import MongoClient, UpdateOne
from dotenv import load_dotenv

# --------- Chargement des variables d'environnement ----------
load_dotenv()
AWS_REGION = os.getenv("AWS_REGION")
S3_BUCKET  = os.getenv("S3_BUCKET_RDY")
MONGO_URI  = os.getenv("MONGO_URI")
MONGO_DB   = os.getenv("MONGO_DB")
MONGO_COL  = os.getenv("MONGO_COL")

# --------- Connexion aux services ----------
s3 = boto3.client("s3", region_name=AWS_REGION)
mongo_client = MongoClient(MONGO_URI)
collection = mongo_client[MONGO_DB][MONGO_COL]

# --------- Récupération du fichier JSONL depuis S3 ----------
def get_latest_ready_file(bucket, prefix=""):
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    if "Contents" not in resp:
        raise FileNotFoundError("Aucun fichier trouvé dans le bucket.")
    latest = max(resp["Contents"], key=lambda x: x["LastModified"])
    return latest["Key"]

key = get_latest_ready_file(S3_BUCKET)
print(f"➡️ Lecture du fichier {key}")

obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
lines = obj["Body"].read().decode("utf-8").splitlines()

# --------- Chargement dans MongoDB ----------
docs = [json.loads(line) for line in lines]

if docs:
    result = collection.insert_many(docs)
    print(f"Migration terminée : {len(result.inserted_ids)} documents insérés")
else:
    print("Aucun document à migrer")