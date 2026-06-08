"""OpenPup interactive TUI configuration menus.

Styled after code-puppy's ``/agent`` / ``/model_settings`` / ``/diff`` menus:
prompt_toolkit arrow-key selectors with a green selection marker, an optional
preview pane, and a cyan hint line. Self-contained so it doesn't reach into
code-puppy internals.
"""

from openpup.tui.menus import run_config_menu

__all__ = ["run_config_menu"]
