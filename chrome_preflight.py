"""Validate the configured Chrome profile without launching a browser.

Never reads profile contents, cookies or credentials. Actual visible session
selection and LinkedIn observations must use supported Computer Use controls.
"""
import ast
import json
from pathlib import Path


def load_config(config_path=None):
    path = Path(config_path or Path(__file__).with_name("chrome_session.json")).expanduser()
    if not path.exists() and path.name == "chrome_session.json" and path.parent.resolve() == Path(__file__).parent.resolve():
        path = path.with_name("chrome_session.example.json")
    config = json.loads(path.read_text())
    if not isinstance(config, dict):
        raise ValueError("Chrome configuration must be a JSON object")
    return config


def preflight(config_path=None):
    config = load_config(config_path)
    if (config["mode"] != "read_only_no_send" or config["allowed_mutations"] != []
            or config["credentials"] is not None or config["launch_second_instance"] is not False
            or config["browser"] != "chrome" or config["max_pages"] != 1):
        raise ValueError("Only no-send session mode is allowed")
    profile = Path(config["user_data_dir"]).expanduser().resolve()
    persistent = config.get("profile_kind") == "connectrobo_persistent"
    create = config.get("create_profile_if_missing", False)
    if not isinstance(create, bool):
        raise ValueError("create_profile_if_missing must be a boolean")
    if persistent and config.get("profile_source") is not None:
        raise ValueError("A ConnectRobo profile must be independent of JobRobo")
    matches = None
    if config.get("profile_source") is not None:
        source = Path(config["profile_source"]).expanduser()
        tree = ast.parse(source.read_text())  # parse, never execute JobRobo configuration
        values = [ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == config["profile_source_key"] for t in n.targets)]
        if len(values) != 1:
            raise ValueError("Expected one literal JobRobo profile setting")
        matches = profile == Path(values[0]).expanduser().resolve()
        if not matches:
            raise ValueError("Configured profile differs from JobRobo")
    if not profile.is_dir() and not (persistent and create and not profile.exists()):
        raise ValueError("Configured profile is missing or is not a directory")
    locks = [name for name in ("SingletonLock", "SingletonSocket", "SingletonCookie")
             if (profile / name).exists() or (profile / name).is_symlink()]
    return {"mode": config["mode"], "jobrobo_profile_matches": matches,
            "profile_kind": "connectrobo_persistent" if persistent else "configured_shared",
            "create_profile_if_missing": persistent and create,
            "user_data_dir": str(profile), "lock_markers": locks,
            "configuration_ready": True, "browser_launched": False,
            "login_verified": False, "actions_executed": 0,
            "next": "Verify active processes and visible chrome://version profile before LinkedIn observation"}


if __name__ == "__main__":
    print(json.dumps(preflight(), indent=2))
