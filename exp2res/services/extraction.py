"""Import-path shim: this module is `exp2res.services.stages` (merged 2026-08-20)."""

import sys

from exp2res.services import stages

sys.modules[__name__] = stages
