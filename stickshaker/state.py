"""The Enabled/Disabled flag, kept in macOS user defaults.

Stored in its own suite (~/Library/Preferences/com.reckoner165.stickshaker.plist),
so it survives daemon restarts. A missing value counts as enabled, and
`install-hooks` sets it back to enabled.
"""

from Foundation import NSUserDefaults

SUITE = "com.reckoner165.stickshaker"
ENABLED_KEY = "enabled"


def _defaults(suite: str) -> NSUserDefaults:
    return NSUserDefaults.alloc().initWithSuiteName_(suite)


def get_enabled(suite: str = SUITE) -> bool:
    value = _defaults(suite).objectForKey_(ENABLED_KEY)
    return True if value is None else bool(value)


def set_enabled(enabled: bool, suite: str = SUITE) -> None:
    _defaults(suite).setBool_forKey_(enabled, ENABLED_KEY)
