# Backup Index V2

CLI Python 3.12 pour administrer les index, mappings, scripts stockés et historiques OpenSearch de la plateforme Backup. Exécution à la demande, sans service permanent ni ingestion métier.

Tasker orchestre les workers. `netbackup-py` fournit les modèles et parsers normalisés ; Backup Collect assurera la projection des documents et leur écriture. Backup Hub exploitera les données. Cette évolution modifie uniquement Backup Index.

Socle commun aux quatre index : `master_server`, `region`, `technology`, `created_at`, `updated_at`, `collected_at` (facultatif) et `data`, avec des types identiques. `created_at` marque la première observation du document (ou de la version pour policies/SLP) ; `updated_at` marque la dernière observation acceptée, même sans changement métier. Policies/SLP ajoutent `hash` et `history`. Voir le [contrat des attributs communs](docs/index-strategy.md#attributs-communs-normalisés).

`master_server` est la clé stable du master dans l’inventaire de Collect, idéalement un UUID. Le futur Collect calculera un `_id` déterministe à partir du type d’objet, du master et de l’identité NetBackup : nom pour policies/SLP, Job ID + date de début pour jobs, Image ID pour images. L’[origine des identifiants et la convention UUID](docs/index-strategy.md#origine-du-_id-de-chaque-index) sont documentées ; ce projet n’effectue pas l’ingestion.

## Installation macOS / RHEL

Depuis le dossier du projet, avec Python 3.12 installé :

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

Pour les tests, installer aussi `python -m pip install -r requirements-dev.txt`. Aucune installation du projet comme paquet n’est nécessaire : conserver le dossier complet et lancer `python main.py --help` depuis sa racine. Voir les guides [macOS](docs/installation-macos.md) et [RHEL](docs/installation-rhel.md). Ne pas remplacer le Python système RHEL, créer de service systemd ou désactiver SELinux.

Depuis un autre dossier, utiliser le chemin absolu du lanceur : `python /chemin/backup-index/main.py validate --all`. Les chemins par défaut restent ancrés à la racine du projet.

Configurer `.env` avant les commandes distantes : URL HTTPS, identifiants CIB et, si nécessaire, `OPENSEARCH_CA_CERT` pointant vers un bundle CA PEM interne. TLS est vérifié par défaut ; aucun contournement automatique n'est effectué. Les mots de passe et corps d'erreurs distants ne sont pas affichés.

Avec `OPENSEARCH_VERIFY_SSL=false`, la désactivation explicite est transmise au client avec `ssl_show_warn=false` pour éviter les warnings TLS répétés. La vérification reste activée par défaut.

| Variable | Défaut / rôle |
| --- | --- |
| `OPENSEARCH_URL` | URL HTTP(S) sans identifiants intégrés |
| `OPENSEARCH_USERNAME`, `OPENSEARCH_PASSWORD` | Identifiants fournis par CIB |
| `OPENSEARCH_VERIFY_SSL` | `true` |
| `OPENSEARCH_CA_CERT` | Bundle CA PEM facultatif |
| `OPENSEARCH_TIMEOUT` | 30 secondes, strictement positif |
| `INDEX_PREFIX` | `backup` |
| `MAPPINGS_DIR` | `mappings` |
| `LOG_LEVEL` | `INFO` |
| `HISTORY_MAX_VERSIONS` | 10 anciennes versions ; contrat de la future ingestion, pas un setting d'index |

Les variables système priment sur `.env`. Les chemins par défaut sont ancrés au projet, indépendamment du répertoire courant. Avec `python main.py --env-file /chemin/.env ...`, les chemins relatifs CA/mappings sont ancrés au dossier de ce fichier. Un préfixe différent nécessite aussi d'adapter `index` et `alias` dans les JSON ; aucun renommage distant n'est effectué.

## Cinq alias, cinq index physiques

| Alias logique — cible future de Collect | Index physique administré |
| --- | --- |
| `backup-jobs` | `backup-jobs-v1` |
| `backup-images` | `backup-images-v1` |
| `backup-images-contents` | `backup-images-contents-v1` |
| `backup-policies` | `backup-policies-v1` |
| `backup-slps` | `backup-slps-v1` |

Chaque fichier contient `index`, `alias`, `settings` et `mappings`. La création ajoute l'index physique et son alias `is_write_index: true` dans la même requête. Les vérifications, modifications de mapping et purges ciblent les noms physiques déclarés, jamais une résolution libre d'alias.

Un ancien index concret nommé `backup-jobs`, un alias vers une autre version, un alias multiple, filtré, routé ou non inscriptible bloque l'opération concernée. Aucun alias existant n'est réaffecté ou supprimé pour réparer un conflit. Un index physique déjà présent **sans son alias** exige une intervention manuelle : `create` ne lui attache pas automatiquement un alias. Un alias existant à cible unique sans flag explicite est accepté s'il est inscriptible et sans options supplémentaires.

`create --all` vérifie les conflits connus sur les cinq paires avant sa première écriture. Les opérations restent séquentielles et ne forment pas une transaction globale : un échec distant ou une course administrative peut laisser un lot partiellement créé. Ne pas administrer les alias/index en concurrence ; les API de création ne proposent pas de condition atomique « cet alias doit toujours être absent ». L'alias est relu après création ; aucune réparation ou suppression compensatoire n'est tentée.

`images-contents` conserve un document par dossier collecté dans le contenu d’une image : uniquement `region`, `master_server`, `image_id`, `path`, `backup_time` et `created_at`. `path` est un keyword pour une recherche exacte sensible à la casse. Le `_id` déterministe sera calculé par Collect à partir de `images-contents`, du master, de l’Image ID et du path. Voir le [contrat détaillé](docs/index-strategy.md#images-contents). Aucun mapping NAS n’est actif.

## Mappings alignés sur le code Python audité

Le code local `netbackup-py` 0.1.0 a été lu dans `/Users/ahmedhm/Documents/NetBackup Module/nbu`. Les décisions détaillées figurent dans [la stratégie et l'audit](docs/index-strategy.md) ; les chemins, empreintes des fichiers et annotations relevées sont conservés dans [model-audit.json](docs/model-audit.json).

Écarts importants avec les champs initialement proposés :

- Jobs : `job_id` (alias Pydantic `id`), `type`, `elapsed_seconds` correspondent à l'export courant du collector, sans `by_alias=True`.
- `Job.status` accepte `int | str | None` : **keyword**, pas integer. `parent_job_id` et `job_id` sont aussi des keyword.
- `percent_complete` est `int | None` : integer.
- `Image.retention_level` accepte chaîne ou entier : keyword. `storage_location` est indexé ; `num_files`, absent du modèle/parser audité, ne l'est pas.
- Les `retention` de policy/SLP sont `Any` : conservées dans `data`. Les opérations SLP et schedules restent aussi dans `data`, sans nested.

Chaque mapping utilise `dynamic: false` et conserve `_source` activé. Les quatre index métier initiaux ont un objet `data` avec `enabled: false` ; `images-contents` conserve uniquement ses six champs. `clients` d'une policy est un keyword pouvant recevoir un tableau de chaînes ; aucun type array n'est inventé. Policies et SLP possèdent `history`, objet non indexé contenant un tableau d'anciennes configurations dans `_source`. Jobs et images n'ont pas d'historique.

Les settings initiaux sont 1 shard / 1 réplique, à valider selon le cluster CIB. Une réplique peut laisser un cluster à un seul nœud jaune. Le mapping ne définit ni identité globale des exécutions ni normalisation de dates : ces contrats doivent être validés avant de développer Collect.

## Commandes

```bash
python main.py list
python main.py validate --all
python main.py ping
python main.py create --all
python main.py check --all
python main.py apply jobs --dry-run
python main.py apply jobs
python main.py info --all
```

Une cible peut être `jobs`, `backup-jobs` ou `backup-jobs-v1` : dans les trois cas, la CLI retrouve le fichier déclaré et applique le mapping au physique. `list` affiche alias et index. `list` et `validate` restent locaux. Tous les JSON sont validés avant les opérations d'index : noms, préfixe, version physique, doublons index/alias, propriétés et settings.

`create` est relançable sur une paire existante correcte ; `EXISTS` ne certifie pas la conformité du mapping. `check` retourne `OK`, `MISSING`, `DIFFERENT` ou `ERROR`, avec l'état de l'alias, champs manquants, incompatibilités, champs distants supplémentaires et différences de settings. Les champs supplémentaires seuls ne bloquent pas. `info` affiche existence du physique, état de l'alias, compte des documents primaires, stockage total/primaires, shards/répliques et nombre de champs, sans lecture des documents.

`apply` ajoute uniquement les champs manquants compatibles et relit le mapping. Tout changement incompatible détecté bloque l'écriture de ce mapping : type différent, modification de `data.enabled`, options significatives différentes. Les settings ne sont jamais modifiés par `apply`. Pour une incompatibilité, préparer un **nouvel index versionné et une migration manuelle** ; aucune migration, réindexation, suppression ou bascule d'alias n'est automatisée. Changer `index` vers `-v2` dans un JSON ne permet pas d'écraser l'alias déjà associé à `-v1` : le conflit sera refusé.

## Scripts stockés

```bash
python main.py scripts list
python main.py scripts check --all
python main.py scripts install --all --dry-run
# Seulement lors d'une action administrative volontaire :
python main.py scripts install --all
# Après examen du diff, remplacement explicite d'une définition différente :
python main.py scripts install --all --replace
```

Trois scripts : `backup_jobs_upsert`, `backup_images_upsert`, `backup_configuration_upsert`. Les IDs restent fixes au niveau du cluster, même avec un autre préfixe d'index. Aucun script n'est installé automatiquement pendant le développement ou une création d'index. Le dry-run affiche les différences sans écrire ; un script différent bloque le lot sans `--replace`. Les résultats installés sont relus.

Les scripts utilisent les champs définitifs des mappings et des observations datées pour rejeter les arrivées obsolètes. Les jobs protègent les états terminaux et autorisent les corrections finales seulement sous option explicite. Les images permettent l'enrichissement, et la correction de valeurs déjà connues avec `allow_corrections=true`. Le script commun policies/SLP compare le hash, archive l'ancien courant uniquement lors d'un changement, puis garde les dix anciennes versions les plus récentes par défaut. A → B → A reste une succession de changements. Le hash sera calculé par Collect.

Le [contrat des paramètres, des états et de Bulk Update](docs/index-strategy.md) est documenté, sans implémenter d'émetteur Bulk dans Backup Index. `HISTORY_MAX_VERSIONS` prépare la configuration de cette future écriture ; Collect devra transmettre `max_history_versions`. Modifier `.env` ne purge ni ne reconfigure automatiquement des documents déjà écrits.

## Purge manuelle de l'historique

```bash
python main.py purge history --keep 5 --dry-run
python main.py purge history --older-than 90d --type slps --dry-run
python main.py purge history --all --dry-run
python main.py purge history --keep 5 --type policies
# Confirmation explicite non interactive :
python main.py purge history --keep 5 --type policies --yes
```

Choisir une seule règle. Sans `--type`, policies et SLP sont ciblés, après vérification de leurs alias. Seuls les tableaux `history` sont modifiés sur les physiques déclarés : jamais le courant, ses dates ou son hash. Prévisualisation des documents et versions concernés ; confirmation interactive ou `--yes` obligatoire avant écriture.

La purge utilise `_update_by_query` limité aux IDs prévisualisés, avec contrôle des métadonnées du snapshot et des conflits natifs. Cent documents par lot et un lot/s par défaut ; `--requests-per-second` accepte une valeur dans ]0,10]. Conflits, noops de concurrence, timeouts et échecs partiels provoquent un code 1 et arrêtent les lots suivants. Les modifications déjà réussies ne sont pas annulées. Le résumé distingue versions prévues et suppressions confirmées. Pas de force merge ni récupération immédiate garantie de l'espace disque. [Détails et limites](docs/index-strategy.md).

## Nettoyage explicite des index de test

Les commandes d'essai existantes sont conservées, mais restent **distinctes** des créations, migrations et purges d'historique :

```bash
python main.py clear jobs --dry-run
python main.py delete jobs --dry-run
python main.py clear jobs       # Confirmation avant suppression de tous les documents
python main.py delete jobs      # Confirmation avant suppression du physique et de son alias
```

`--yes` confirme explicitement sans interaction. Une seule cible déclarée, aucun `--all` ni wildcard. `clear` conserve mapping/settings ; sur policies/SLP, il supprime aussi les configurations courantes. `delete` supprime le physique et l'alias qui lui est attaché ; cette conséquence est annoncée dans le plan/confirmation. Les fichiers JSON et scripts stockés restent disponibles. Ce sont des opérations irréversibles, jamais déclenchées automatiquement par les autres commandes.

Les protections existantes (confirmation, nom concret, contrôle d'UUID, vérification du résultat) sont conservées. Les écritures concurrentes peuvent laisser de nouveaux documents après un clear ; arrêter les collectes pour les essais. Un timeout peut survenir après le début d'une suppression : vérifier l'état avant toute relance. Pour un ancien index logique en conflit, une intervention administrative extérieure à cette CLI sera nécessaire ; il n'est pas une cible physique déclarée de cette configuration.

## Catalogue et audit

Les [quatre catalogues NetBackup](catalog/netbackup/) contiennent **350 entrées** : 276 références documentaires conservées et 74 attributs de modèles Python relevés dans le code. Ce ne sont pas 350 concepts distincts. Chaque entrée indique son origine, son type, sa sélection et sa disponibilité connue. Les champs `python_type`, `opensearch_type` et `indexed` décrivent les projections ; les entrées purement documentaires ne prétendent pas avoir un type Python vérifié.

Les catalogues ne génèrent jamais les mappings. Les attributs complexes non indexés sont conservés dans `data` par la future projection Collect. Voir [les sources et limites documentaires](docs/netbackup-attributes.md), notamment la référence REST 8.1.1 plus ancienne que la bibliothèque auditée.

## Responsabilités de Backup Collector et OpenSearch

Backup Index administre les mappings, alias et scripts stockés. Backup Collector collecte les données et demande leur écriture ; OpenSearch exécute les règles des scripts sur chaque document.

| Responsabilité | Composant |
| --- | --- |
| Collecter, normaliser et valider les données avant envoi | Backup Collector |
| Calculer le `_id` déterministe selon le contrat documenté | Backup Collector |
| Calculer `hash` pour policies/SLP à partir d’une représentation stable de la configuration métier, sans métadonnées de collecte | Backup Collector |
| Fournir `updated_at`, choisir le script et envoyer les opérations Bulk | Backup Collector |
| Détecter l’absence du document et l’insérer | OpenSearch, via le script d’upsert |
| Rejeter les observations anciennes ou de même date | Script OpenSearch, par comparaison de `updated_at` |
| Comparer `hash`, archiver la version précédente et limiter l’historique des policies/SLP | Script OpenSearch |
| Protéger les identités, transitions de jobs et corrections des images selon le contrat | Script OpenSearch |
| Initialiser et préserver `created_at` selon le type d’objet | Script OpenSearch, sauf pour images-contents |
| Examiner chaque résultat Bulk et gérer les erreurs, conflits concurrents et reprises bornées | Backup Collector |

Pour jobs, images, policies et SLP, Collector envoie une action Bulk `update` avec le `_id`, `scripted_upsert: true`, `upsert: {}` et le script stocké correspondant. Il n’a pas besoin de lire chaque document avant écriture : le script décide de l’insertion, de la mise à jour ou du noop dans l’opération OpenSearch. Les écritures concurrentes peuvent produire des conflits de version ; Collector doit les traiter avec des reprises bornées et réutiliser le même `_id` et la même observation lors d’une reprise. Le script vérifie le format de `hash`, sans recalculer son contenu ; le calcul correct reste à la charge de Collector.

Pour `images-contents`, aucun script d’upsert n’est installé. Collector peut utiliser l’action Bulk `create` avec le `_id` déterministe et `created_at` fixé à la première insertion. Un résultat individuel `409` de cette action signifie que le document existe déjà : le conserver préserve `created_at` et évite les doublons. Cette stratégie ne met pas à jour les autres attributs d’un dossier existant. Ne pas confondre ce doublon avec un conflit concurrent d’une action `update`, qui relève de la gestion des reprises. Contrôler les résultats de chaque élément Bulk, pas uniquement le statut HTTP global.

## Tests et validation avant création

```bash
python -m pytest -q
python main.py validate --all
# Après configuration CIB, vérifications en lecture seulement :
python main.py ping
python main.py check --all
python main.py scripts check --all
python main.py scripts install --all --dry-run
```

La suite normale utilise des mocks et ignore les tests d'intégration. Les tests Painless et alias réels sont préparés dans `tests/test_opensearch_integration.py`, avec opt-in, adresse fixe de boucle locale et vérification du nom de cluster. **Ils n'ont pas été exécutés sur cette révision** : les mocks et tests de contrats ne prouvent pas la compilation/exécution des nouveaux scripts. Aucune connexion ou modification de cluster n'est effectuée pendant cette implémentation.

Avant une création volontaire, valider les droits CIB, la topologie, les conflits d'alias, les unités des compteurs, les dates ISO-8601, l'identité stable des exécutions/copies et le contrat des scripts dans un environnement de qualification dédié.

Codes de sortie : 0 succès ; 1 exécution/connexion/échec partiel ; 2 configuration, validation ou incompatibilité (`check` non conforme inclus). `python main.py --help` affiche toutes les commandes.

## Structure

```text
main.py             lanceur de la CLI
app/                configuration, validation/comparaison, index/alias, scripts, purge, CLI
requirements.txt    dépendances d’exécution
requirements-dev.txt dépendances des tests
pytest.ini          configuration des tests
mappings/           jobs.json, images.json, images-contents.json, policies.json, slps.json
scripts/            trois scripts Painless
catalog/netbackup/  quatre catalogues indépendants
tests/              tests unitaires et intégration locale opt-in
docs/               installations, stratégie, audit et sources
```

## Dépannage

TLS : vérifier le bundle CA et le nom DNS sans désactiver automatiquement la vérification. 401 : vérifier les identifiants ; 403 : faire vérifier les permissions CIB, notamment lecture/création d'alias et administration des scripts. `CONFLICT` : examiner le nom logique et sa cible, sans tentative de réparation automatique. `DIFFERENT` : lire les différences de mapping/settings et d'alias. Une erreur Painless doit être diagnostiquée sur l'environnement de qualification, sans exposer des corps contenant des secrets dans les logs de cette CLI.
