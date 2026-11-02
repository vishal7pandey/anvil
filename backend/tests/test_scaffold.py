import importlib


def test_subpackages_import():
    for name in ("anvil.api", "anvil.runtime", "anvil.tools", "anvil.kb"):
        importlib.import_module(name)
