# Weather Data Pipeline – GreenAndCoop

Pipeline de données météo de bout en bout pour alimenter une base **MongoDB** à partir de sources hétérogènes (Excel, JSONL.gz sur S3), avec transformation, contrôle qualité, et déploiement d’une stack minimaliste sur **AWS** (EC2, CloudWatch, S3, ECR).
Conçu pour le projet Forecast 2.0 chez GreenAndCoop. 

## Objectifs

* Intégrer de nouvelles stations météo semi-pro dans la base commune. 
* Normaliser et fusionner les formats hétérogènes.
* Charger dans MongoDB et vérifier la qualité post-migration.
* Mesurer la latence d’accès pour l’exploitation DS.

## Schéma d’architecture

![Pipeline météo – GreenAndCoop](logigramme.png)

## Structure du repo

```
.
├─ docker-compose.yml            # MongoDB local + service ETL
├─ etl/
│  ├─ Dockerfile.etl
│  ├─ requirements.txt
│  ├─ excel_to_csv.py            # Excel -> CSV (S3 raw -> S3 csv)
│  ├─ data_transform.py          # Normalisation / fusion (S3 staging -> ready)
│  ├─ migration_to_mongoDB.py    # S3 ready -> MongoDB
│  └─ check_quality_migration.py # Contrôles ligne/champ + stats comparées
├─ infra/
│  ├─ provider.tf                # provider AWS
│  ├─ main.tf                    # EC2, SG, CloudWatch Agent, user_data
│  └─ outputs.tf
└─ test_latency.py               # Mesure latence de requête Mongo
```

## Modèle de données cible (extraits)

Collection `weather` (exemples de champs) :
`station_id, station_name, city, latitude, longitude, elevation, software, date, time, temperature, dew_point, humidity, wind, speed, gust, pressure, precip_rate, precip_accum, uv, solar, nebulosite, temps_omm…` (schéma détaillé présenté dans le diaporama). 

## Exécution locale avec Docker Compose

Prérequis : Docker Desktop.

```bash
# À la racine du projet
docker compose up --build
```

Le service **etl** enchaîne :

1. `excel_to_csv.py` → Excel S3 raw → CSV S3 csv
2. `data_transform.py` → JSONL.gz S3 staging → JSONL prêt S3 ready
3. `migration_to_mongoDB.py` → chargement Mongo
4. `check_quality_migration.py` → contrôle qualité post-migration

MongoDB est exposé en `localhost:27017` avec l’utilisateur root défini dans `docker-compose.yml`.

## Déploiement AWS avec Terraform

Prérequis : Terraform, AWS CLI configuré.

```bash
cd infra
terraform init
terraform plan \
terraform apply
```

Ce module crée :

* **EC2** avec script `user_data` installant Docker et l’agent **CloudWatch** (CPU, mémoire, disque, collecte de logs).
* **Security Group** ouvrant SSH 22 et Mongo 27017. **À restreindre** à votre IP publique en production.
* Sortie `ec2_public_ip` pour accéder à Mongo et au service.

> L’image ETL peut être poussée sur **ECR** puis tirée sur l’EC2 si vous ciblez une exécution 100 % cloud.

## Contrôles qualité

`check_quality_migration.py` calcule :

* Taux d’erreur par ligne et par champ entre la source « ready » et Mongo.
* Comparaisons de moyennes sur variables numériques pour détecter des dérives.
* Score global agrégé.

Sortie lisible en console et exploitable via CloudWatch Logs si vous redirigez les logs sur EC2.

## Mesure de latence

`test_latency.py` exécute une requête type sur `weather` et imprime le nombre de documents et le temps de réponse. Paramétrez `MONGO_URI` avant usage.

## Airbyte (optionnel)

Le flux amont peut être alimenté par **Airbyte** pour extraire les sources météo vers S3 raw/staging, comme prévu dans la présentation du projet. 

## Sécurité

* Restreindre l’accès 27017 à des IP de confiance.
* Ne jamais committer `.env` et clés SSH.
* Activer le chiffrement S3 côté serveur et des politiques IAM minimales.

## Roadmap courte

* Tests unitaires sur fonctions de transformation.
* Schéma JSON formel pour `weather`.
* Dockerisation complète côté EC2 avec ECR.
* Dashboard CloudWatch des métriques ETL/Mongo.
