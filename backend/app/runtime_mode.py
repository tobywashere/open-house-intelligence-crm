"""Native-only is an explicit installation boundary, independent of model health."""
import os


def native_only() -> bool:
    value=os.environ.get('OHI_NATIVE_ONLY','0')
    if value not in ('0','1'):
        raise RuntimeError('OHI_NATIVE_ONLY must be 0 or 1')
    return value=='1'


def validate_runtime_mode(auth_config):
    if native_only() and auth_config.mode!='capabilities':
        raise RuntimeError('Native-only setup requires capability authentication')
