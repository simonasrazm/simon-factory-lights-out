"""SFLO security configuration — toggle resolution from pipeline.yaml."""



# Security toggle keys. Unknown keys are silently ignored.
SECURITY_KEYS = (
    "isolate_user_settings",
    "isolate_all_settings",
    "no_session_persistence",
    "sandbox_config_dir",
    "require_permission",
    "wipe_sandbox",
)


def load_security_config():
    """Return the security toggle dict from pipeline.yaml.

    Reads the `security:` section. All keys default to False if the
    section is missing or pipeline.yaml is unavailable.
    """
    config = {k: False for k in SECURITY_KEYS}
    truthy = {"true", "yes", "on", "1"}

    try:
        from .config import load_pipeline_config

        pipeline_sec = load_pipeline_config().get("security", {})
        for key in SECURITY_KEYS:
            val = str(pipeline_sec.get(key, "false")).strip().lower()
            if val in truthy:
                config[key] = True
    except Exception:
        pass  # pipeline.yaml unavailable — all-false defaults

    return config
