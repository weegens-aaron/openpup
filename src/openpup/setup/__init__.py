"""On-rails setup wizard for OpenPup.

``openpup setup`` walks you through obtaining and validating credentials for
each platform, with step-by-step instructions, clickable URLs, and a LIVE
validation call against each service before anything is written to ``.env``.
"""

from openpup.setup.wizard import run_setup_wizard

__all__ = ["run_setup_wizard"]
