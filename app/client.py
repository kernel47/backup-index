"""Client officiel ; les erreurs distantes ne sont jamais affichées brutes."""
from opensearchpy import OpenSearch
from opensearchpy.exceptions import (
    AuthenticationException, AuthorizationException, ConnectionTimeout,
    SSLError, ConnectionError, TransportError,
)

from app.config import Settings


def create_client(settings: Settings) -> OpenSearch:
    return OpenSearch(
        hosts=[settings.opensearch_url],
        http_auth=(settings.opensearch_username, settings.opensearch_password.get_secret_value())
        if settings.opensearch_username else None,
        verify_certs=settings.opensearch_verify_ssl,
        ssl_show_warn=settings.opensearch_verify_ssl,
        ca_certs=settings.opensearch_ca_cert or None,
        timeout=settings.opensearch_timeout,
        max_retries=0,
    )


def error_message(error: Exception) -> str:
    if isinstance(error, AuthenticationException):
        return "Authentification OpenSearch refusée (401)."
    if isinstance(error, AuthorizationException):
        return "Permissions OpenSearch insuffisantes (403)."
    if isinstance(error, SSLError):
        return "Erreur TLS : vérifier le certificat CA et le nom du serveur."
    if isinstance(error, ConnectionTimeout):
        return "Délai de connexion OpenSearch dépassé."
    if isinstance(error, ConnectionError):
        return "Connexion OpenSearch impossible : vérifier réseau, DNS et adresse."
    if isinstance(error, TransportError):
        return f"Erreur HTTP OpenSearch ({error.status_code})."
    return "Erreur d'exécution OpenSearch ; vérifier configuration et accès."
