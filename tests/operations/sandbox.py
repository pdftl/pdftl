import sys
from unittest.mock import patch


class ModuleSandboxMixin:
    """
    POLITE NUCLEAR OPTION:
    1. Saves the state of sys.modules (snapshot).
    2. Wipes 'pdftl' so THIS test gets a fresh isolate.
    3. Restores sys.modules afterward so other tests find what they expect.
    """

    def setUp(self):
        # 1. Start a patcher for sys.modules.
        #    This automatically saves a copy of the current dictionary.
        self._modules_patcher = patch.dict(sys.modules)
        self._modules_patcher.start()

        # 2. Wipe your project from the CURRENT (patched) sys.modules
        #    This forces your test to load fresh copies (Version 2).
        keys_to_delete = [k for k in sys.modules if k.startswith("pdftl")]
        for k in keys_to_delete:
            del sys.modules[k]

        super().setUp()

    def tearDown(self):
        super().tearDown()
        # 3. Stop the patcher.
        #    This discards all the changes (and fresh imports) made by this test
        #    and puts the Original (Version 1) modules back into sys.modules.
        self._modules_patcher.stop()
