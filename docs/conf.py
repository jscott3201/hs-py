"""Sphinx configuration for hs-py documentation."""

import sys
from importlib.metadata import version as _pkg_version
from pathlib import Path

# -- Path setup --------------------------------------------------------------
# Add the source directory so autodoc can import modules without requiring
# the package to be installed.

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

project = "hs-py"
author = "hs-py contributors"
copyright = "2026, hs-py contributors"  # noqa: A001

version = _pkg_version("hs-py")
release = version

# -- General configuration ---------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinx_autodoc_typehints",
    "myst_parser",
]

master_doc = "index"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
language = "en"

# -- Autodoc -----------------------------------------------------------------

autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_typehints_format = "short"
autodoc_default_options = {
    "members": True,
    "show-inheritance": True,
    "undoc-members": False,
}

# -- sphinx-autodoc-typehints ------------------------------------------------

always_use_bars_union = True
typehints_defaults = "braces"

# -- Intersphinx -------------------------------------------------------------

intersphinx_mapping = {
    "python": ("https://docs.python.org/3.13", None),
}

# -- HTML output -------------------------------------------------------------

html_theme = "furo"
html_static_path = ["_static"]
html_title = f"hs-py {version}"

# -- Warnings ----------------------------------------------------------------
# Suppress ambiguous cross-reference warnings from autodoc resolving
# unqualified names across modules.

suppress_warnings = ["ref.python"]
