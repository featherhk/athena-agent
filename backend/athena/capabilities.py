"""Observed execution location, independent of the user's frontend device."""
from pathlib import Path
import shutil
import sys


def manual_browser_handoff_available(config, driver):
    """A server/headless browser has no window the person can take over."""
    return (not config.execution_socket and sys.platform == "darwin"
            and bool(getattr(driver, "headed", False)) and driver.available())


def execution_capabilities(config):
    hosted = bool(config.execution_socket)
    command = Path(config.execution_socket).is_socket() if hosted else sys.platform == "darwin" and bool(shutil.which("sandbox-exec"))
    from .browser_driver import browser_driver
    driver = browser_driver(config)
    return {"execution_node": "cloud" if hosted or sys.platform == "linux" else "local",
            "os": sys.platform, "commands": command,
            "command_scope": "account_container" if hosted else "local_protected_process",
            "browser": driver.available(),
            "browser_manual_handoff": manual_browser_handoff_available(config, driver),
            "user_help": True, "computer_control": False,
            "controls_user_phone": False,
            "files": "Current account workspace; generated files appear in the conversation."}
