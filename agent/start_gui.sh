#!/bin/bash
# Chinese input (IME) note:
# The system IME is fcitx4, but its Qt6 bridge plugin is incompatible with the
# Qt 6.12 bundled in PySide6 (undefined Qt_6_PRIVATE_API symbol), so CJK input
# does not work with QT_IM_MODULE=fcitx. PySide6's ibus plugin loads fine, so
# we route this app through ibus instead.
# One-time setup (see README "Chinese input" section):
#   sudo apt install ibus ibus-libpinyin
#   im-config -n ibus   # or set ibus as the default IME in settings
#   (libpinyin must be in ibus's engine list - checked/fixed below)
source .venv/bin/activate
export QT_IM_MODULE=ibus
export GTK_IM_MODULE=ibus
export XMODIFIERS=@im=ibus

# ibus only uses the engines listed in its dconf "engines" setting; by default
# that is just the US keyboard, so even with ibus-libpinyin installed the tray
# only offers English. Make sure libpinyin is in the list.
if command -v dconf >/dev/null 2>&1; then
    engines=$(dconf read /desktop/ibus/general/engines 2>/dev/null)
    if ! [[ "$engines" == *libpinyin* ]]; then
        echo "[start_gui] ibus engine list has no CJK engine; adding libpinyin..."
        dconf write /desktop/ibus/general/engines "['xkb:us::eng', 'libpinyin']"
        dconf write /desktop/ibus/general/primary-engine-order "['libpinyin', 'xkb:us::eng']"
        echo "[start_gui] done. If the tray still shows only English, log out and back in."
    fi
fi

python -m coder_agent.gui.app
