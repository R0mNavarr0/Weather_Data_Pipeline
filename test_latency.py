import time
from pymongo import MongoClient

MONGO_URI = "mongodb://root:rootpsw@13.36.208.34:27017/greenandcoop?authSource=admin"   

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
db = client.get_database()
col = db["weather"]

query = {
    "station_id": "07015",
    "date": {"$regex": "^2024-10-05"}
}

t0 = time.monotonic()
docs = list(col.find(query))
t1 = time.monotonic()

print(f"Documents trouvés : {len(docs)}")
print(f"Temps de requête : {(t1 - t0)*1000:.2f} ms")