"""FormuNex（方枢）backend package."""
from .env_compat import alias_legacy_environ as _alias_legacy_environ

# Promote legacy FORMUMIND_* environment variables before any module reads os.environ.
_alias_legacy_environ()
