# Stratégie des index, audit Python et contrat d’écriture

## Audit du dépôt existant

Les modules de configuration/TLS, validation/comparaison, scripts stockés, purge et commandes d’essai existaient déjà. Ils sont conservés. Les adaptations portent sur les mappings, leurs alias, les noms acceptés par les scripts, les cibles physiques de maintenance, les catalogues et les tests.

Le code **netbackup-py 0.1.0** est accessible dans `/Users/ahmedhm/Documents/NetBackup Module`. Fichiers lus : `nbu/models/{jobs,images,policies,slp}.py`, leurs quatre parsers, `nbu/models/base.py`, `nbu/collectors/base.py` et `pyproject.toml`. Le dossier source `nbu/` a été privilégié aux copies de `build/lib/`. Cet audit est statique : aucun test du dépôt NetBackup n’a été lancé et aucun de ses fichiers n’a été modifié.

[model-audit.json](model-audit.json) enregistre les chemins, SHA-256 des fichiers lus, annotations, sélections et décisions au 24 octobre 2026. C’est une photographie de l’état local, pas une dépendance de la CLI ni une preuve de disponibilité de chaque attribut sur CIB.

| Proposition initiale | Code audité | Décision de mapping |
| --- | --- | --- |
| Jobs `id` | `Job.job_id: int \| str`, alias Pydantic `id` | `job_id: keyword`, conforme à l’export actuel du collector sans `by_alias=True` |
| Jobs `job_type` | `Job.type: str \| None` | `type: keyword` |
| Jobs `elapsed_time` | `Job.elapsed_seconds: int \| None` | `elapsed_seconds: long` |
| Jobs `status: integer` | `int \| str \| None` | `status: keyword`, pour accepter les deux représentations sans imposer un entier |
| Jobs `percent_complete: float` | `int \| None` | `integer` |
| Jobs `policy_type`, `schedule_type` | Pas de champs déclarés ni d’alimentation dans `parse_job` | Pas de propriétés indexées initiales ; les éventuels attributs complémentaires restent dans `data` |
| Images `retention_level: integer` | `str \| int \| None` | `keyword` |
| Images `num_files` | Absent du modèle/parser audité | Non indexé, à confirmer avant ajout |
| Images `storage_location` | `str \| None`, alimenté par le parser | `keyword` |
| Policies/SLP `retention` | `Any`, pouvant notamment être simplifié par les parsers | Conservé dans `data`, sans type scalaire supposé |
| SLP `state`, `slp_version` des anciennes versions du projet | Le modèle expose `active`, `status`, `target_storage` | Indexer ces champs réels ; ne pas inventer de version native |
| `parse_content` annoncé | Aucun `def parse_content` trouvé dans le `nbu/` local inspecté | Aucun mapping NAS/contenu pour cette étape |

Le modèle de base utilise `extra="allow"` et expose `source`/`raw`. Ces valeurs ne sont pas identiques à `master_server`, qui identifie le master/système de collecte. Ne pas promouvoir automatiquement les extras en champs indexés. Le futur Collect devra construire une enveloppe : métadonnées de collecte, champs sélectionnés, puis modèle métier complet ou compléments sous `data`. Il ne doit pas envoyer aveuglément tout le `model_dump` à la racine.

## Noms physiques et alias

| Fichier | Alias logique | Physique initial |
| --- | --- | --- |
| jobs.json | backup-jobs | backup-jobs-v1 |
| images.json | backup-images | backup-images-v1 |
| images-contents.json | backup-images-contents | backup-images-contents-v1 |
| policies.json | backup-policies | backup-policies-v1 |
| slps.json | backup-slps | backup-slps-v1 |

Collect écrira ultérieurement via les alias. Backup Index administre exclusivement les physiques déclarés. Le JSON de configuration contient une clé singulière `alias` propre à cet outil ; `create` la traduit en l’objet OpenSearch `aliases: {"backup-jobs": {"is_write_index": true}}` lors de la création du physique.

Les noms doivent respecter `INDEX_PREFIX` et `index == alias + "-vN"`, avec N entier positif. Les doublons et collisions entre noms physiques et logiques sont refusés localement. La CLI accepte un nom court, logique ou physique, uniquement s’il correspond à un fichier déclaré.

Avant écriture, la cible est vérifiée : aucun wildcard ni index système, et le physique doit être concret, pas un alias ou flux. L’alias doit désigner uniquement ce physique, sans filtre/routage et avec écritures permises. Une cible unique sans flag `is_write_index` explicite est acceptée, car elle est implicitement inscriptible. Les autres options d’alias sont traitées conservativement comme un conflit.

`create` ne remplace jamais un index existant. Les cas suivants sont refusés sans réparation : ancien index concret au nom logique, alias vers une autre version ou plusieurs physiques, alias filtré/routé/non inscriptible, physique déjà existant sans alias. Si une paire correcte existe, le résultat `EXISTS` ne vérifie pas son mapping : utiliser `check`.

Pour `create --all`, les cinq paires sont pré-vérifiées avant la première écriture. La vérification est répétée au moment de chaque création. Index neuf et alias neuf sont créés dans une même requête, puis l’alias est relu. Aucune API de réaffectation/suppression d’alias n’est utilisée pour réparer une erreur. Les créations ne forment pas une transaction globale ; ne pas modifier alias/index en concurrence. L’API ne fournit pas de condition atomique d’absence de l’alias : une administration concurrente sort du contrat de cette V2. Un échec après création est signalé sans suppression compensatoire.

La CLI administre aussi `images-contents`. Aucun mapping NAS n’est actif. Le retrait d’un fichier local n’a aucun effet sur un ancien index distant.

Sources OpenSearch : [Create Index et aliases](https://docs.opensearch.org/latest/api-reference/index-apis/create-index/), [Get Alias](https://docs.opensearch.org/latest/api-reference/alias/get-alias/), [écritures via alias](https://docs.opensearch.org/latest/im-plugin/index-alias/).

## Champs actifs

Tous les mappings déclarent `_source.enabled: true`, `dynamic: false`, et `data: {"type":"object","enabled":false}`. `data` conserve les structures JSON sans indexation détaillée ; `_source` conserve aussi les éventuels champs non indexés. Un ajout de champ à un document ne modifie pas automatiquement le mapping.

### Jobs

- keyword : `master_server`, `region`, `technology`, `job_id`, `parent_job_id`, `type`, `state`, `status`, `policy`, `client`, `schedule`, `storage_unit`, `backup_id`.
- date : `start_time`, `end_time`, `created_at`, `updated_at`, `collected_at`.
- long : `elapsed_seconds`, `kilobytes`, `files`.
- integer : `percent_complete`.
- `data` non indexé ; aucun historique.

`status_description` reste dans le modèle complet sous `data`. `status` en keyword permet le filtrage/agrégation par valeur exacte, mais pas des calculs numériques ou une plage numérique. L’absence d’un contrat entier strict justifie ce choix. Les identifiants doivent être sérialisés de façon cohérente, idéalement comme chaînes sans perte de zéros initiaux ; aucune conversion irréversible n’est effectuée par Backup Index.

### Images

- keyword : `master_server`, `region`, `technology`, `image_id`, `backup_id`, `client`, `policy`, `policy_type`, `schedule`, `schedule_type`, `retention_level`, `storage_location`.
- date : `backup_time`, `expiration_time`, `created_at`, `updated_at`, `collected_at`.
- integer : `copy_number`.
- long : `kilobytes`.
- `data` non indexé ; aucun historique, aucun contenu détaillé de fichiers.

`retention_level` conserve sa représentation exacte. Ne pas la confondre avec une durée de rétention ou supposer qu’un niveau entier correspond au même délai sur chaque master.

### Policies

- keyword : `master_server`, `region`, `technology`, `name`, `policy_type`, `slp`, `storage`, `clients`, `hash`.
- boolean : `active`, `storage_is_slp`.
- date : `created_at`, `collected_at`, `updated_at`.
- objets non indexés : `data`, `history`.

`clients` reçoit une liste de chaînes, sans type array ou nested. Les `schedules`, `backup_selections`, `retention`, `backup_copies`, `start_window`, `include_dates`, `exclude_dates` et autres détails restent sous `data`. Ne pas mettre dans `slp` un stockage qui n’est pas une SLP ; le parser normalisé expose déjà `storage_is_slp` et `slp`.

### SLP

- keyword : `master_server`, `region`, `technology`, `name`, `status`, `target_storage`, `hash`.
- boolean : `active`.
- date : `created_at`, `collected_at`, `updated_at`.
- objets non indexés : `data`, `history`.

La liste `operations` reste intégralement dans `data`, avec les champs des objets `SLPOperation` : operation, target_storage, retention, retention_type, duplication, operation_index, source_operation_index, storage. Aucun nested n’est nécessaire au besoin actuel. Aucun numéro de version native n’est inventé.

## Exemples de documents cohérents avec les modèles

Ces exemples montrent la future projection de Collect, pas une ingestion implémentée ici. Les métadonnées `master_server`/région/technologie/dates de collecte sont ajoutées par Collect. Les dates Python de type `datetime | str` doivent être validées et sérialisées en ISO-8601 UTC avant l’envoi. Une chaîne libre admise par Pydantic n’est pas nécessairement une date OpenSearch valide.

Jobs :

```json
{
  "master_server": "master-exemple",
  "technology": "netbackup",
  "job_id": "42",
  "type": "BACKUP",
  "state": "DONE",
  "status": "0",
  "client": "client-exemple",
  "start_time": "2026-10-24T01:00:00.000Z",
  "end_time": "2026-10-24T01:05:00.000Z",
  "elapsed_seconds": 300,
  "kilobytes": 1024,
  "files": 20,
  "percent_complete": 100,
  "updated_at": "2026-10-24T01:06:00.000Z",
  "data": {"job_id":"42","type":"BACKUP","state":"DONE","status":0,"status_description":"exemple","source":"api","raw":{}}
}
```

Images :

```json
{
  "master_server": "master-exemple",
  "image_id": "client-exemple_1792803600",
  "backup_id": "client-exemple_1792803600",
  "client": "client-exemple",
  "policy": "POL_EXEMPLE",
  "backup_time": "2026-10-24T01:00:00.000Z",
  "expiration_time": "2026-11-24T01:00:00.000Z",
  "retention_level": "1",
  "copy_number": 1,
  "storage_location": "STORAGE_EXEMPLE",
  "kilobytes": 1024,
  "updated_at": "2026-10-24T01:06:00.000Z",
  "data": {"image_id":"client-exemple_1792803600","retention_level":1,"source":"api","raw":{}}
}
```

Policy (le hash est un placeholder à calculer, pas une valeur utilisable) :

```json
{
  "master_server": "master-exemple",
  "name": "POL_EXEMPLE",
  "active": true,
  "clients": ["client-exemple"],
  "storage": "SLP_EXEMPLE",
  "storage_is_slp": true,
  "slp": "SLP_EXEMPLE",
  "hash": "<sha256-hexadecimal-minuscule-64-caracteres>",
  "updated_at": "2026-10-24T01:06:00.000Z",
  "data": {"name":"POL_EXEMPLE","active":true,"clients":["client-exemple"],"schedules":[],"backup_selections":["/srv"],"retention":{"value":30,"unit":"DAYS"},"source":"api","raw":{}}
}
```

SLP :

```json
{
  "master_server": "master-exemple",
  "name": "SLP_EXEMPLE",
  "active": true,
  "status": "active",
  "target_storage": "STORAGE_EXEMPLE",
  "hash": "<sha256-hexadecimal-minuscule-64-caracteres>",
  "updated_at": "2026-10-24T01:06:00.000Z",
  "data": {"name":"SLP_EXEMPLE","active":true,"status":"active","target_storage":"STORAGE_EXEMPLE","retention":"30 DAYS","operations":[{"operation":"BACKUP","target_storage":"STORAGE_EXEMPLE","retention":"30 DAYS","duplication":false,"operation_index":0}],"source":"api","raw":{}}
}
```

`history` et `created_at` des configurations sont gérés par le script, pas transmis dans son paramètre `document`. Les exemples de `data` montrent des sous-ensembles cohérents ou des compléments ; Collect devra choisir un contrat stable entre modèle complet et compléments.

## Images-contents

Alias `backup-images-contents`, index physique `backup-images-contents-v1`. Un document représente un dossier présent dans le contenu réel d’une image NetBackup, obtenu par la commande CLI de collecte, et non une entrée de backup selection. La collecte peut être partielle : l’absence d’un dossier dans cet index ne prouve pas son absence dans l’image.

| Attribut | Type | Origine / sens |
| --- | --- | --- |
| `region` | keyword | Région du master, fournie par Collect |
| `master_server` | keyword | Même clé stable du master que dans `backup-images` |
| `image_id` | keyword | Identifiant NetBackup de l’image parcourue |
| `path` | keyword | Chemin exact du dossier retourné par la commande CLI |
| `backup_time` | date | Date native du backup de l’image, identique à celle de `backup-images` |
| `created_at` | date | Date de première insertion du dossier par Collect, conservée lors des répétitions |

Le mapping reste limité à ces six attributs, avec `dynamic: false` et `_source` activé. Aucun `depth`, `data`, `history`, `updated_at`, ni script d’upsert supplémentaire. `path` n’est ni découpé ni converti en minuscules : la recherche exacte préserve la casse et les séparateurs. Collect devra conserver une représentation stable du chemin pour retrouver la même identité, sans troncature silencieuse. Un chemin dépassant la limite d’un terme keyword doit être signalé par la collecte.

Les détails complémentaires de l’image sont recherchés dans `backup-images` avec le couple `(master_server, image_id)` ; `image_id` seul n’est pas une clé globale. `backup_time` est volontairement dupliqué pour filtrer les contenus par date sans recherche préalable des images. Cet index contient potentiellement beaucoup de documents ; Collect écrira par lots et réutilisera le même `_id` pour éviter les doublons, en préservant `created_at` des documents existants. Backup Index administre le mapping, sans exécuter cette ingestion.

```json
{
  "region": "region-exemple",
  "master_server": "master-exemple",
  "image_id": "client-exemple_1792803600",
  "path": "/srv/projets/archives",
  "backup_time": "2026-10-24T01:00:00.000Z",
  "created_at": "2026-10-24T01:06:00.000Z"
}
```

## Attributs communs normalisés

Les quatre index métier initiaux partagent le même socle (`images-contents` utilise le sous-ensemble minimal décrit ci-dessus), en `snake_case`, avec des types identiques :

| Attribut | Type | Signification |
| --- | --- | --- |
| `master_server` | keyword | Identifiant stable et unique du master NetBackup, fourni par l’inventaire de Collect |
| `region` | keyword | Région de la source |
| `technology` | keyword | Technologie de sauvegarde |
| `created_at` | date | Première observation du document, ou de la version courante pour policies/SLP |
| `updated_at` | date | Dernière observation acceptée, même sans changement métier |
| `collected_at` | date | Date de récupération par le collecteur, facultative |
| `data` | object, enabled false | Données métier conservées sans indexation de leurs sous-champs |

Pour jobs/images, `created_at` reste fixe après insertion. Pour policies/SLP, il est renouvelé uniquement lors d’un changement de configuration ; la version précédente conserve ses dates dans l’historique. Dans ces quatre index, `updated_at` avance à chaque observation acceptée. Ces deux index partagent aussi `hash` (keyword) et `history` (object, enabled false).

Chaque attribut métier partagé conserve le même nom et le même type entre index : notamment `backup_id`, `client`, `policy`, `policy_type`, `schedule` et `kilobytes`. Les noms propres aux modèles NetBackup, comme `start_time` ou `backup_time`, restent ceux du contrat audité. Le nom `status` désigne le statut natif de l'objet concerné, sans supposer un vocabulaire identique entre jobs et SLP.

`observed_at` et `last_seen_at` sont remplacés par `created_at` et `updated_at`. Le collecteur transmet `updated_at` comme date de l’observation ; les scripts initialisent `created_at` à cette date et en préservent la sémantique. `created_at` est réservé aux scripts. Ces dates ne sont ni la date native de création dans NetBackup, ni l’heure d’écriture OpenSearch. Les documents et historiques existants utilisant les anciens noms exigent une migration explicite avant utilisation de ces scripts ; `apply` ajoute les champs sans migrer les données. Un champ déjà présent dans un index distant reste un champ supplémentaire ; `apply` ne supprime ni mapping ni données existantes. Les scripts stockés doivent être réinstallés explicitement avec `--replace` pour adopter le nouveau contrat.

## Identité et fraîcheur

### Origine du `_id` de chaque index

`master_server` remplace `source_id` dans le contrat. Malgré son nom, sa valeur est l’identifiant stable du master dans l’inventaire de Collect, idéalement un UUID attribué une seule fois et conservé lors d’un changement de hostname. Il ne provient pas du champ `source` des modèles Python (qui décrit API/CLI). Collect ajoute cette valeur à tous les documents issus de la connexion au master concerné. Les exemples `master-exemple` représentent cette clé d’inventaire, pas un hostname à recalculer.

Le `_id` OpenSearch sera un UUID déterministe calculé par **Backup Collect**, à partir du type d’objet et de son identité métier :

| Index logique | Composants de l’identité | Origine des composants |
| --- | --- | --- |
| `backup-policies` | `policies`, `master_server`, `name` | Inventaire du master et `Policy.name` renvoyé par NetBackup |
| `backup-slps` | `slps`, `master_server`, `name` | Inventaire du master et `SLP.name` renvoyé par NetBackup |
| `backup-jobs` | `jobs`, `master_server`, `job_id`, `start_time` | Inventaire du master, `Job.job_id` (alias natif `id`) et date native de début de l’exécution |
| `backup-images` | `images`, `master_server`, `image_id` | Inventaire du master et `Image.image_id` renvoyé par NetBackup |
| `backup-images-contents` | `images-contents`, `master_server`, `image_id`, `path` | Inventaire du master, identifiant de l’image parcourue et chemin du dossier retourné par la commande CLI |

Convention à implémenter dans Collect : UUID v5 avec le namespace fixe `uuid5(NAMESPACE_URL, "urn:backup-index:documents:v1")`. Le nom à hacher est un tableau JSON des composants du tableau ci-dessus, tous convertis en chaînes, sérialisé en UTF-8 avec `ensure_ascii=False` et `separators=(",", ":")`. Utiliser un tableau évite les ambiguïtés d’une concaténation avec séparateur. Le type d’objet fait partie de l’identité ; le préfixe d’index, sa version physique, les dates de collecte et le hash de configuration n’en font pas partie.

```python
import json
from uuid import NAMESPACE_URL, uuid5

namespace = uuid5(NAMESPACE_URL, "urn:backup-index:documents:v1")
components = ["policies", "master-exemple", "policy-exemple"]
document_id = str(uuid5(namespace, json.dumps(
    components, ensure_ascii=False, separators=(",", ":")
)))
```

Ainsi, deux masters distincts produisent des identités distinctes pour un même nom ou Job ID ; une collecte répétée retrouve le même document. Un renommage de policy/SLP crée une nouvelle identité, tandis qu’un changement de configuration conserve le `_id` et alimente `history`. L’UUID est utilisé comme `_id` OpenSearch, sans champ `uuid` supplémentaire dans le mapping. Il est distinct de l’UUID interne d’un index physique utilisé par les protections administratives.

Pour les jobs, Collect devra confirmer que `job_id` et `start_time` identifient bien une exécution, y compris lors des reprises/réutilisations. Sérialiser `job_id` de manière stable et normaliser `start_time` en UTC ISO-8601 avec une précision fixe pour que la même date produise la même identité. Ne jamais fabriquer `start_time` à partir de la date de collecte lorsqu’elle manque. Le script utilise ce champ comme garde d’identité et refuse les observations sans les informations indispensables.

Le contrat ci-dessus représente un document par image. Si un document par copie est retenu plus tard, `copy_number` devra être ajouté à l’identité avec adaptation des protections du script ; ne pas mélanger les deux granularités. L’ingestion et le calcul d’UUID ne sont pas exécutés par Backup Index : les scripts contrôlent les composants métier, mais ne calculent ni ne vérifient le `_id` fourni par Collect. Les données existantes avec `source_id` exigent une migration explicite avant utilisation du nouveau contrat ; `apply` ne renomme pas les champs existants.

Les dates du contrat de script sont des chaînes UTC ISO-8601 à format stable, recommandées à la milliseconde. Elles sont comparées via `Instant`, pas comme des chaînes. Les horloges doivent être synchronisées. Une observation <= au watermark est ignorée. À date égale, le premier état accepté est conservé ; aucune règle de départage arbitraire n’est inventée. Les dates futures erronées ne sont pas corrigées automatiquement.

## Scripts d’upsert

### Génération depuis les mappings

Les fichiers `scripts/*.painless` sont des modèles métier contenant un marqueur `/*__CONTRACT__*/`. Ils ne doivent pas être installés directement. `app/script_generation.py` remplace ce marqueur par le contrat généré depuis `mappings.properties` et `ingestion` du JSON correspondant. `app/ingestion.py` valide les règles ; `app/scripts.py` compare et installe les sources générées. Les commandes `backup-index scripts list/check/install` utilisent le dossier `MAPPINGS_DIR` et le préfixe configurés, y compris avec `--env-file`.

`ingestion.required` liste les champs obligatoires non nuls (chaînes non vides). `ingestion.identity` liste les composants obligatoires et immuables de l’identité ; elle doit conserver les composants métier indispensables. `ingestion.mutable` déclare les champs jobs/images dont une valeur connue peut évoluer, sous les protections métier existantes. Un nouveau champ de mapping est autorisé automatiquement et fixe par défaut. Une valeur manquante peut être enrichie. `created_at`/`history` restent gérés par le script ; les dates de suivi et `data` conservent leur traitement dédié. Les identités ne peuvent pas être déclarées modifiables.

Policies/SLP ont des contrats générés indépendamment, dans le même script `backup_configuration_upsert` et sélectionnés par `params.kind`. Elles conservent le remplacement de snapshot et l’historisation par hash ; leur liste `mutable` est absente ou vide. Le hash de Collector doit couvrir les nouveaux champs métier. `images-contents` ne reçoit aucun script ni règle d’ingestion supplémentaire.

La génération est déterministe et effectuée en mémoire sans publication implicite. Après modification du JSON : valider, appliquer les ajouts de mapping, prévisualiser les différences de scripts puis les publier avec `backup-index scripts install --all --replace`. Ni l’index physique ni l’alias ne changent automatiquement. `ingestion` est une métadonnée locale, jamais envoyée à OpenSearch. Les trois IDs de scripts et les paramètres attendus par Collector restent stables.

Trois IDs stables : `backup_jobs_upsert`, `backup_images_upsert`, `backup_configuration_upsert`. L’installation est explicite ; un script différent demande `--replace` après affichage du diff. Les installations de scripts ne sont pas transactionnelles ; l’API ne propose pas de compare-and-swap atomique, donc sérialiser les interventions administratives.

Le contrat utilise `scripted_upsert: true` avec `upsert: {}`. La branche `ctx.op == 'create'` traite explicitement l’insertion initiale. Les branches d’Update Document utilisent `ctx.op = 'none'` pour produire un résultat noop. Les scripts ne font aucune requête HTTP et n’accèdent à aucun autre document ou index.

Exemple de forme Bulk pour la future écriture (placeholders à remplacer, une ligne d’action puis une ligne de corps, retour à la ligne final) :

```json
{"update":{"_index":"backup-jobs","_id":"<identite-execution-confirmee>","retry_on_conflict":5}}
{"scripted_upsert":true,"upsert":{},"script":{"id":"backup_jobs_upsert","params":{"document":{"master_server":"master-exemple","job_id":"42","start_time":"2026-10-24T01:00:00.000Z","updated_at":"2026-10-24T01:01:00.000Z","state":"ACTIVE","data":{}}}}}
```

Backup Index ne fournit pas d’émetteur Bulk. Collect devra gérer les erreurs item par item et les conflits avec retries bornés. Lors d’un retry, le script est évalué de nouveau sur le dernier état. Une observation devenue ancienne produit alors un noop.

### Jobs

Paramètres : `document`, `allow_final_corrections` facultatif (false), `allowed_transitions` facultatif, `terminal_states` facultatif.

Identité requise : `master_server`, `job_id`, `start_time`, `updated_at` et état non vide. Les champs acceptés sont ceux du mapping actif sauf `created_at`, géré par le script. Le code local expose un état `str | None`, sans enum : le script ne traduit pas les libellés natifs. Par défaut, il autorise QUEUED/PENDING → ACTIVE ou DONE et ACTIVE → DONE, ainsi qu’une observation plus récente du même état. Un autre changement d’état nécessite une table `allowed_transitions` explicitement validée. `DONE` reste terminal même si une table personnalisée tente de le rouvrir ; des terminaux supplémentaires peuvent être transmis dans `terminal_states`.

Les attributs fixes déjà connus (`technology`, `region`, `parent_job_id`, `type`, `policy`, `client`, `schedule`, `storage_unit`) ne sont pas modifiables pour la même exécution ; les absents peuvent être enrichis. Les états/codes, dates de fin et compteurs peuvent évoluer avant le terminal. Après un terminal, les valeurs absentes peuvent être enrichies, mais remplacer une valeur connue exige `allow_final_corrections=true`. Une observation incompatible est ignorée dans son ensemble, sans modification partielle.

`data` est fusionné au premier niveau avec les valeurs non nulles : une liste ou un objet imbriqué est remplacé comme une valeur entière, sans fusion récursive. Un null n’efface pas une valeur connue. Une observation plus récente du même contenu fait avancer le watermark ; une requête répétée de même date est un noop.

### Images

Paramètres : `document`, `allow_corrections` facultatif (false). Identité requise : `master_server`, `image_id`, `updated_at`.

Une observation plus récente peut enrichir un champ absent. Remplacer une valeur métier connue exige sa présence dans `ingestion.mutable` et `allow_corrections=true`, y compris pour expiration, copie ou stockage ; cela permet des corrections contrôlées sans supposer que l’image est éternellement immuable. Un champ fixe reste protégé même avec cette option. Les arrivées anciennes sont ignorées. Le master et l’identité d’image ne changent jamais à `_id` constant. Les champs omis/null ne suppriment pas les valeurs existantes. `data` utilise la même fusion de premier niveau et l’option de correction. Aucun champ `state` d’image n’est inventé dans cette révision alignée sur le modèle actuel.

### Policies et SLP

Paramètres : `document`, `kind` (policies/slps), `max_history_versions` facultatif (entier 0..2147483647, défaut 10).

Champs requis : master_server, name, updated_at, hash hexadécimal minuscule de 64 caractères et `data` objet. Le script vérifie le format du hash, pas la justesse de son calcul. La configuration reçue est un snapshot métier complet, contrairement aux enrichissements partiels jobs/images. Les clés `history`, `created_at` sont réservées au script.

- Création : courant reçu, history vide, created_at initialisé à updated_at.
- Observation <= updated_at (ou created_at pour un document plus ancien) : noop.
- Hash identique : aucun historique ajouté ; avancer seulement updated_at et collected_at si fourni. Conserver created_at de la version courante.
- Hash différent : copier l’ancien courant sans son history dans les anciennes versions, remplacer le courant, trier l’historique chronologiquement et conserver les N dernières anciennes configurations.
- A → B → A : conserver A puis B comme anciennes versions. Pas de déduplication globale par hash.

Le hash SHA-256 sera calculé par Collect sur une projection métier normalisée et complète, couvrant aussi les champs métier indexés. Exclure dates techniques, raw volatil et autres métadonnées de collecte selon un contrat explicite. Ne pas exclure aveuglément les dates d’effet métier. Trier les clés des objets ; préserver les listes ordonnées et trier seulement les listes dont la sémantique d’ensemble est confirmée. Un changement de normalisation est un changement de contrat.

Cet historique décrit les changements observés par les collectes, pas une reconstruction exhaustive de toutes les configurations NetBackup passées. `HISTORY_MAX_VERSIONS=10` prépare le contrat de la future ingestion : Collect devra le transmettre en paramètre. Changer `.env` ne reconfigure pas des appels extérieurs et ne purge pas les historiques existants. La limite s’applique lors d’un prochain changement ; utiliser la purge explicite pour réduire l’existant.

## Purge manuelle conservée

`purge history` reste limité aux physiques des policies/SLP déclarés. Les alias sont vérifiés avant le plan puis avant l’écriture ; aucun alias n’est modifié. La purge n’efface jamais le courant ou un document complet.

Règles exclusives : `--keep N`, `--older-than Nd`, `--all` ; option `--type policies|slps`, sinon les deux. `--keep` conserve les N anciennes versions les plus récentes ; l’âge compare strictement les dates d’observation au seuil UTC fixé au début de la commande. Dates invalides refusées pendant la prévisualisation.

Le scroll lit seulement les métadonnées d’historique, les deux dates de suivi et les IDs/versions. `history` n’étant pas indexé, la prévisualisation doit parcourir les documents ; le plan conserve en mémoire les IDs et métadonnées concernés. Le CLI affiche les versions prévues à retirer, puis exige confirmation ou `--yes` explicite.

`_update_by_query` vise uniquement les IDs prévisualisés des index physiques. Le script compare les deux dates de suivi et la liste ordonnée des couples created_at/hash à celles du plan. Le `ctx._version` exposé dans le contexte Update By Query de l’environnement 2.19.4 testé lors d’une révision antérieure valait -1 : il n’est pas utilisé comme garde. Les changements d’historique doivent être reflétés par leurs hashes selon le contrat d’ingestion. Les conflits natifs entre le snapshot de l’API et l’écriture sont traités avec `conflicts=proceed` puis signalés.

Le script réaffecte seulement `ctx._source.history`. Cent documents par lot, un lot/s par défaut, throttling natif et limitation entre appels. L’option `--requests-per-second` est bornée dans ]0,10]. Aucun scheduler ni force merge. Les modifications déjà réussies ne sont pas annulées après un échec. Le résumé distingue volumes prévus, documents modifiés et versions confirmées seulement pour les lots entièrement réussis. Un conflit, noop inattendu, timeout ou échec partiel produit un code 1 et arrête les lots suivants. Après erreur réseau, le résultat du lot interrompu peut être inconnu ; refaire un état des lieux avant relance.

`clear`/`delete` restent des commandes d’essai séparées et explicitement confirmées. `clear` efface tous les documents et donc aussi les courants ; `delete` retire le physique et l’alias qui lui est attaché. Elles ne sont jamais appelées par la purge, la création ou une migration. Voir le README pour leurs protections.

## Évolution compatible et migrations

`check` compare récursivement les champs et options significatives, ainsi que les settings attendus et l’alias. Il normalise certains defaults serveur, dont `_source.enabled=true`. Les métadonnées de settings non demandées sont ignorées. Les champs distants supplémentaires seuls ne bloquent pas ; aucun champ n’est supprimé.

`apply` ajoute les nouveaux champs compatibles, y compris sous un objet/multi-champ existant, après détection de toutes les incompatibilités de ce mapping. Un ajout comme storage_unit ou priority est possible ; changer kilobytes de long en object ou activer l’indexation de data est refusé et demande un nouvel index versionné. Les settings ne sont pas modifiés et le résultat du mapping est relu. Ajouter un champ ne remplit pas rétroactivement les anciens documents.

Le passage à une autre version physique et la bascule d’alias sont des opérations manuelles hors du périmètre de migration de cette CLI. Ne pas effacer l’ancien index ni transformer un ancien nom logique concret pour faire passer une création. L’outil détecte ces conflits et s’arrête. La validation locale est volontairement raisonnable ; OpenSearch reste l’autorité finale pour les combinaisons de paramètres, analyseurs et plugins.

## Tests et limites de validation de cette révision

La suite normale utilise des clients simulés pour validation, création physique/alias, conflits, ajouts et incompatibilités, dry-runs, purge, confirmations et commandes existantes. Des tests de contrat vérifient la cohérence des listes de champs des scripts avec les mappings. Ils ne compilent pas Painless et ne démontrent pas son exécution.

Les tests réels sont dans `tests/test_opensearch_integration.py` et restent opt-in. Ils utilisent uniquement `http://127.0.0.1:19280`, exigent le nom de cluster `backup-index-local-test`, ne lisent jamais `.env`, et créent des index dédiés `backup-test-<uuid>-...-v1`. Les scénarios couvrent scripted upsert initial/Bulk, fraîcheur, terminaux/corrections, A/B/A, dix versions, concurrence, conservation du courant pendant purge et création physique/alias. Les index de test ne sont pas supprimés par ces tests.

```bash
python -m pytest -q
# Uniquement après préparation manuelle de l’environnement de test isolé :
BACKUP_INDEX_RUN_INTEGRATION=1 python -m pytest -q tests/test_opensearch_integration.py
```

Aucun cluster n’a été contacté ou modifié pendant cette révision. Les tests d’intégration adaptés doivent donc encore être exécutés en qualification ; les succès d’une ancienne version de scripts ne valident pas ces nouvelles définitions. Avant de créer les index sur CIB, confirmer les droits d’alias/scripts, la topologie shards/répliques, l’identité des exécutions et des copies, les dates, les unités et les tables de transitions.
