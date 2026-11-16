from pathlib import Path

from megooflags_api.settings import Settings

NODE_TOKEN_PREFIX = "mf_node_"


def worker_config_errors(settings: Settings) -> list[str]:
    """What stops a worker from starting; empty when it can start."""
    errors: list[str] = []
    url = settings.control_plane_url
    if url is None:
        errors.append(
            "MEGOOFLAGS_CONTROL_PLANE_URL is not set: set it to the control plane's URL, "
            "e.g. https://flags.example.com"
        )
    if settings.node_token is None:
        errors.append(
            "MEGOOFLAGS_NODE_TOKEN is not set: "
            "create the worker on the Nodes page and copy its token"
        )
    elif not settings.node_token.get_secret_value().startswith(NODE_TOKEN_PREFIX):
        errors.append(
            f"MEGOOFLAGS_NODE_TOKEN is not a node token (it starts with {NODE_TOKEN_PREFIX})"
        )
    if url is not None and url.scheme != "https" and not settings.control_plane_insecure:
        errors.append(
            "MEGOOFLAGS_CONTROL_PLANE_URL must use https://; "
            "set MEGOOFLAGS_CONTROL_PLANE_INSECURE=true to allow http on a private network"
        )
    ca_file = settings.control_plane_ca_file
    if ca_file and not Path(ca_file).is_file():
        errors.append(f"MEGOOFLAGS_CONTROL_PLANE_CA_FILE {ca_file} does not exist")
    return errors
