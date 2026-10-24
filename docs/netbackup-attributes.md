# Catalogue des attributs NetBackup

Les quatre fichiers `catalog/netbackup/{jobs,policies,slps,images}.json` sont des références documentaires éditables manuellement. Ils ne sont jamais chargés par les commandes d'administration des index, et aucune génération automatique des mappings n'est implémentée.

Le relevé initial du 24 octobre 2026 contient **350 entrées** : 98 jobs, 120 policies, 30 SLP, 102 images. Les 276 entrées documentaires initiales sont conservées et complétées par 74 attributs Python vérifiés. Une même information peut avoir plusieurs entrées REST/CLI : ce sont des emplacements documentés distincts, pas 350 concepts métier uniques. Le catalogue vise une couverture raisonnable des sources accessibles ; il ne prétend pas couvrir tous les agents, extensions ou versions de NetBackup.

## Audit Python complémentaire

Le code local netbackup-py 0.1.0 a été inspecté dans `/Users/ahmedhm/Documents/NetBackup Module/nbu` : quatre modèles/parsers, modèle de base et collector de base. Les entrées `source_type: "python"` portent une référence fichier/ligne, l’annotation exacte (`python_type`), le type retenu (`opensearch_type`), `indexed` et la confiance `source_verified`. Les annotations, empreintes et décisions sont enregistrées dans [model-audit.json](model-audit.json) et expliquées dans [la stratégie](index-strategy.md).

Les chemins absolus sont la provenance de cet audit local ; ils ne sont pas des dépendances de fonctionnement sur RHEL. Les catalogues sont une photographie manuelle, pas une synchronisation automatique avec un autre dépôt. L’audit Python est statique ; aucun test ni fichier de netbackup-py n’est modifié.

Les noms/type Python priment pour les mappings : job_id/type/elapsed_seconds, status mixte en keyword, retention_level mixte en keyword, percent_complete entier. Les retention de type Any restent dans data. Les champs proposés mais absents (dont num_files d’image) ne sont pas déclarés comme propriétés indexées.

## Sources et limites de vérification

- [Référence officielle REST NetBackup 8.1.1](https://www.veritas.com/support/en_US/doc/NBU_811_API_REF) : le téléchargement direct a fourni un PDF de 142 pages, consulté pour les schémas Jobs (p. 27–35), Images et fragments (p. 44–50), Policies Oracle, clients et schedules (p. 86–97). L'outil Web échoue à ouvrir cette URL mais le téléchargement direct a fonctionné. Des ligatures du PDF dégradent l'extraction de certains noms ; les noms ont été recoupés avec les exemples et les tables. Cette référence reste ancienne.
- [Exemples officiels NetBackup API](https://github.com/VeritasOS/netbackup-api-code-samples) et [helper Go de policies](https://github.com/VeritasOS/netbackup-api-code-samples/blob/master/recipes/go/utils/api_helper.go) : ils complètent notamment les attributs VMware. Les champs provenant uniquement d'un exemple de création restent marqués comme tels, sans garantie qu'une réponse GET les contienne.
- [bpdbjobs, référence officielle](https://www.veritas.com/support/en_US/doc/15263389-127350397-0/v14660746-127350397) : les colonnes 1 à 14 de `-all_columns` ont été relevées dans la référence consultable par recherche. La page directe renvoie parfois 403 ; la version exacte n'a pas été confirmée et reste `null`. Les colonnes variables supplémentaires ne sont pas inventées.
- [bppllist](https://www.veritas.com/support/en_US/doc/15263389-157469019-0/v14665087-157469019) et [exemples officiels bppolicynew/bppllist](https://origin-www.veritas.com/content/support/en_US/doc/15263389-142519608-0/v14665633-142519608) : libellés de sortie texte. La page directe bppllist renvoie parfois 403 ; les exemples officiels restent identifiés séparément.
- [bpimagelist, NetBackup 8.0](https://www.veritas.com/content/support/en_US/doc/123533878-127136857-0/v123538598-127136857) : le contenu de `-idonly` est documenté, sans déduction de chemins JSON depuis les options de filtre.
- [nbstl, référence officielle](https://www.veritas.com/support/en_US/doc/15263389-161340675-0/v14671031-161340675) : libellés de l'exemple `-L` et consultation de la version native via `-b -all_versions`. Le layout exact du numéro de version n'est pas vérifié. [Autre référence officielle nbstl](https://origin-www.veritas.com/content/support/en_US/doc/15263389-127350397-0/v14671031-127350397) consultée pour recouper le fonctionnement des versions.
- [nbstlutil, NetBackup 9.1](https://www.veritas.com/content/support/en_US/doc/15263389-147313249-0/v14671124-147313249) : l'exemple officiel de sortie `list -U` décrit des **images et copies gérées par SLP**, pas le schéma de configuration SLP. Ces entrées sont donc dans le catalogue images.
- [Versions natives SLP](https://www.veritas.com/content/support/en_US/doc/ka6j00000004FbrAAE) : l'ouverture directe indiquait une maintenance, mais la version indexée de la documentation officielle décrit les versions natives et les commandes de consultation. Rien ne permet d'assimiler ces versions aux changements observés dans nos documents.

La référence API 10.1 recherchée à l'ancienne adresse SORT a retourné 404. Aucun schéma OpenAPI/Swagger officiel récent de SLP n'a pu être vérifié. Le dépôt d'exemples ne fournit pas de spécification universelle remplaçant les références de version. Les schémas reconstitués sur des sites tiers n'ont pas été utilisés comme autorité. La version et les réponses réelles du cluster **NetBackup CIB** restent inconnues ; aucune connexion à NetBackup n'a été faite.

## Convention des entrées

| Champ | Interprétation |
| --- | --- |
| `name` | Nom natif du champ REST ou libellé/valeur documentée CLI |
| `path` | Chemin de réponse REST GET détail ; `[]` indique un tableau ; `null` pour une sortie CLI sans chemin JSON vérifié |
| `location` | Emplacement exact ou description explicite de l'emplacement connu ; ne remplace pas une position inconnue par une position supposée |
| `description` | Résumé français du sens documenté |
| `source`, `source_reference` | Référence officielle, URL et éventuellement page du PDF |
| `netbackup_version` | Version confirmée dans la source, sinon `null` |
| `source_type`, `collection_method` | API et endpoint, ou CLI et format précis |
| `data_type` | Type documenté/interprétation de la valeur affichée ; `unknown` si non confirmé, `date-time` représente une chaîne de date |
| `suggested_opensearch_type` | Suggestion à examiner, jamais appliquée automatiquement ; peut rester `null` |
| `category` | Catégorie métier pour faciliter la revue |
| `selected`, `mapping_field` | Projection déjà retenue dans le mapping normalisé ; sinon `false` et `null` |
| `confidence` | `documented` pour une référence ; `official_example` pour un exemple officiel ; `source_verified` pour un attribut lu dans le code Python local |
| `availability` | Confirmation dans la référence et disponibilité CIB (`unknown` tant que non vérifiée) |
| `notes` | Conditions, ambiguïtés de type/unité et transformations restant à définir |

Les entrées documentaires possèdent aussi les clés `python_type`, `opensearch_type`, `indexed` : `python_type` reste null lorsqu’aucune correspondance avec un modèle n’a été établie. Les attributs non indexés de modèles imbriqués sont décrits par leur chemin dans le modèle, sans les promouvoir en nested OpenSearch.

Les champs internes (`master_server`, dates de collecte, hash de configuration, historique) ne sont pas inventés comme attributs NetBackup. `selected=true` n'implique pas que la conversion soit déjà développée. La projection du `storage` d'une policy vers `slp` n'est valide que si `storageIsSLP=true`.

## Points à examiner avec les vrais JSON

1. Confirmer la version NetBackup, le type de policy et la version de média REST utilisée.
2. Séparer les champs réellement renvoyés des paramètres de filtre et des corps de mutation.
3. Valider l'identité des exécutions, les dates natives et la normalisation des états.
4. Confirmer la sémantique de `kilobytes` dans les modèles et parsers : `kilobytesTransferred` est estimé dans la référence Jobs, `kilobytesDataTransferred` est décrit comme réel. Le `kbytes` d'une image et son transfert réseau ne sont pas interchangeables. La référence 8.1.1 décrit même `kbytesDataTransferred` en bytes malgré son nom : l'unité reste à confirmer.
5. Vérifier les divergences entre schémas et exemples : `kilobytesTransfered` (un r) apparaît dans un exemple Jobs ; plusieurs booléens y apparaissent en 0/1 ; `effectiveDateUTC` est entier dans le schéma Oracle et une date ISO dans l'exemple VMware. Aucune uniformisation silencieuse n'est faite.
6. Aucun champ slp_version n’est exposé par le modèle SLP audité : il n’est pas indexé. Les opérations et rétentions restent dans data ; target_storage est un champ normalisé scalaire indexé.
7. Examiner les champs de sécurité/immutabilité disponibles dans la version CIB. Les schémas consultés exposent notamment chiffrement et sécurité étendue, sans constituer une preuve de champs WORM/immutabilité pour les versions récentes ; aucun champ WORM supposé n'a été ajouté.

Pour ajouter un attribut : compléter manuellement son entrée et sa preuve, choisir la normalisation, puis modifier séparément le mapping actif. Une modification de `selected` seule n'a aucun effet sur OpenSearch.
