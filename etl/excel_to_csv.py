import pandas as pd
from dotenv import load_dotenv
import boto3, os, io, gzip

load_dotenv()

AWS_REGION = os.getenv("AWS_REGION")
S3_BUCKET = os.getenv("S3_BUCKET_RAW")
S3_BUCKET_CSV = os.getenv("S3_BUCKET_CSV")

s3 = boto3.client("s3", region_name=AWS_REGION)

# Dictionnaire de métadonnées par station
stations_info = {
    "LaMadeleine": {
        "Station_ID" : "ILAMAD25",
        "Station_Name": "La Madeleine",
        "Latitude": 50.659,
        "Longitude": 3.07,
        "Elevation": 23,
        "City": "LaMadeleine",
        "Software": "EasyWeatherPro_V5.1.6"
    },
    "Ichtegem": {
        "Station_ID" : "IICHTE19",
        "Station_Name": "WeerstationBS",
        "Latitude": 51.092,
        "Longitude": 2.999,
        "Elevation": 15,
        "City": "Ichtegem",
        "Software": "EasyWeather_V1.6.6"
    }
}

resp = s3.list_objects_v2(Bucket=S3_BUCKET)

for obj in resp.get("Contents", []):
    key = obj["Key"]
    if key.lower().endswith(".xlsx"):
        print(f"Traitement de {key}...")

        body = s3.get_object(Bucket=S3_BUCKET, Key=key)["Body"].read()

        xls = pd.ExcelFile(io.BytesIO(body))

        # Identifier la station à partir du nom de fichier
        station_id = None
        for sid in stations_info.keys():
            if sid in key:
                station_id = sid
                break

        if not station_id:
            print(f"⚠️ Station inconnue pour {key}, pas d'enrichissement.")
            continue

        for sheet in xls.sheet_names:
            df = pd.read_excel(xls, sheet_name=sheet)

            df["Date"] = sheet
            df["Station_ID"] = station_id
            for col, val in stations_info[station_id].items():
                df[col] = val

            csv_buffer = io.BytesIO()
            df.to_csv(csv_buffer, index=False)

            base = os.path.basename(key).replace(".xlsx", "")
            csv_key = f"{base}_{sheet}.csv"

            s3.put_object(
                Bucket=S3_BUCKET_CSV,
                Key=csv_key,
                Body=csv_buffer.getvalue()
            )
            print(f"  -> CSV uploadé : {csv_key}")