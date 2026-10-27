# Installation Red Hat Enterprise Linux

Utiliser un Python 3.12 parallèle fourni par les dépôts RHEL approuvés ou votre distribution interne CIB. Selon la version de RHEL et les dépôts activés, le paquet peut être disponible ainsi :

```bash
sudo dnf install python3.12 python3.12-pip
python3.12 --version
```

Si ces paquets ne sont pas disponibles, demander à l'administrateur la procédure approuvée pour la version exacte de RHEL. Ne pas remplacer le Python système ni modifier ses liens. Aucun changement de SELinux n'est requis.

Déployer le dossier source dans un emplacement accessible au compte d'administration, puis, depuis ce dossier :

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python install.py
cp .env.example .env
chmod 600 .env
```

Utiliser l'index de paquets interne approuvé si l'accès PyPI est interdit. Ne pas utiliser `sudo pip`.

Pour une installation sans accès à un index de paquets, utiliser un dossier de wheels préparé pour la version Python et la plateforme RHEL cibles :

```bash
python -m pip install --no-index --find-links /chemin/wheelhouse -r requirements.txt
python install.py
```

Aucune installation du projet comme paquet n’est nécessaire. Conserver `main.py`, `app/`, `mappings/` et `scripts/` ensemble. `python install.py` installe le lanceur dans le venv actif ; le réexécuter si le projet change de dossier. Depuis un autre dossier, lancer aussi `backup-index validate --all` avec le venv activé ; les chemins par défaut restent ancrés à la racine du projet.

Configurer les variables de `.env` : adresse HTTPS, identifiants fournis par CIB et chemin vers le bundle CA interne PEM. Par exemple `OPENSEARCH_CA_CERT=/chemin/approuve/ca-cib.pem`. Le compte doit pouvoir lire ce fichier ; sa gestion et son renouvellement suivent les procédures CIB. Garder `OPENSEARCH_VERIFY_SSL=true`. Il n'est pas nécessaire de modifier le magasin de certificats global pour fournir ce bundle au client.

```bash
backup-index validate --all
backup-index ping
backup-index list
backup-index create --all
backup-index check --all
backup-index info --all
backup-index apply jobs --dry-run
```

Valider les shards/répliques dans les JSON avant `create` et examiner le dry-run avant `apply jobs`. Les commandes de création et d'application modifient le cluster ciblé. Aucun service systemd n'est créé : l'outil est exécuté à la demande.

Pour une configuration externe au code :

```bash
backup-index --env-file /chemin/config/.env check --all
```

Configurer `MAPPINGS_DIR` en chemin absolu ou relatif au dossier de ce `.env`. Les variables d'environnement système priment sur le fichier. Conserver le dossier du projet complet, notamment `mappings/` et `scripts/`.

Tests sans cluster :

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Voir le README pour les codes de sortie, les permissions à faire autoriser et le dépannage réseau, TLS, 401 et 403.

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
