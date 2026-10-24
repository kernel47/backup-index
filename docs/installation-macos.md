# Installation macOS

Installer Python 3.12 à partir d'une distribution approuvée par votre organisation (par exemple l'installateur officiel Python). Vérifier sa présence :

```bash
python3.12 --version
```

Dans le dossier du projet :

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
chmod 600 .env
```

Dans `.env`, renseigner l'URL HTTPS du cluster, le nom d'utilisateur et le mot de passe autorisés par CIB. Obtenir le fichier CA interne PEM auprès de CIB et définir, par exemple, `OPENSEARCH_CA_CERT=/chemin/ca-cib.pem`. Garder `OPENSEARCH_VERIFY_SSL=true` et vérifier que le nom de l'URL correspond au certificat. Utiliser un chemin absolu pour un certificat partagé.

```bash
backup-index validate --all
backup-index ping
backup-index list
backup-index create jobs
backup-index check jobs
backup-index info jobs
backup-index apply jobs --dry-run
```

`ping` et les commandes distantes nécessitent l'accès réseau CIB. `create` crée réellement un index s'il est absent ; valider les valeurs initiales de shards/répliques avant de l'utiliser. Les exemples partent du dossier du projet. Une fois l'environnement activé, les chemins par défaut restent stables depuis un autre dossier.

Pour une configuration distincte :

```bash
backup-index --env-file /chemin/config/.env validate --all
```

Les chemins relatifs CA/mappings sont alors résolus depuis ce fichier. Pour les tests locaux :

```bash
pip install -e '.[test]'
python -m pytest -q
```

## Administration V2

Après configuration, examiner les scripts sans les installer implicitement :

```bash
backup-index scripts list
backup-index scripts check --all
backup-index scripts install --all --dry-run
backup-index purge history --keep 5 --dry-run
```

Ces dry-runs ne modifient pas le cluster. L’installation réelle des scripts exige une commande explicite ; la purge réelle demande confirmation ou `--yes`. `HISTORY_MAX_VERSIONS=10` prépare le contrat de la future ingestion, sans modifier les mappings ni purger périodiquement les données. Voir [la stratégie V2](index-strategy.md) et [le catalogue documentaire](netbackup-attributes.md).

## Index physiques et alias

`create jobs` prépare `backup-jobs-v1` et son alias `backup-jobs` dans une même requête. Les cinq types utilisent ce modèle versionné ; les écritures futures de Collect utiliseront les alias. Un ancien index concret au nom logique ou un alias pointant ailleurs bloque la création, sans réparation automatique. Les index existants sans leur alias ne sont pas rattachés automatiquement. Valider ces conflits, les types audités et les scripts en qualification avant une création volontaire. Aucun mapping NAS n’est actif dans cette étape.
