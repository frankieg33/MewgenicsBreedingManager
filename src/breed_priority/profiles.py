"""Breed Priority — profile management UI and logic.

Standalone functions for building and updating the profile selector bar,
serialising/deserialising profile state, and handling load/save/delete.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
    QPushButton, QDialog, QMessageBox,
)

from .delegates import _ConfirmDialog, _ProfileNameEdit
from .theme import (
    CLR_STATE_SELECTED_BG, CLR_STATE_SELECTED_FG, CLR_STATE_SELECTED_BORDER,
    CLR_INTERACTIVE, CLR_INTERACTIVE_BDR,
    CLR_TEXT_LABEL_COUNT, CLR_TEXT_LABEL_GROUP,
    CLR_SURFACE_APP_MAIN, CLR_SURFACE_HEADER_BORDER, CLR_SURFACE_SCORE_AREA,
    CLR_SURFACE_PANEL, CLR_SURFACE_SEPARATOR,
)
from .styles import checkbox_style


_NUM_PROFILES = 5


def build_profile_bar(
    parent,
    profile_name_text,
    profile_traits_only,
    on_name_changed,
    on_traits_only_changed,
    on_btn_clicked,
    on_load,
    on_save,
    on_delete,
) -> tuple:
    """Build the profile selector bar UI.

    Args:
        parent: Parent QWidget.
        profile_name_text: Current profile name string.
        profile_traits_only: Whether "Only Trait Desirability" is checked.
        on_name_changed: Callback for name edit text changes.
        on_traits_only_changed: Callback for traits-only checkbox.
        on_btn_clicked: Callback(n) for profile slot button clicks.
        on_load/on_save/on_delete: Callbacks for action buttons.

    Returns:
        (bar_widget, widget_refs) — widget_refs is a dict with keys:
            name_edit, sel_arrow_lbl, sel_name_lbl, profile_btns,
            load_btn, save_btn, delete_btn, loaded_lbl, dirty_lbl,
            chk_traits_only
    """
    bar = QWidget()
    bar.setStyleSheet(f"background:{CLR_SURFACE_APP_MAIN}; border-bottom:1px solid {CLR_SURFACE_HEADER_BORDER};")
    vb = QVBoxLayout(bar)
    vb.setContentsMargins(0, 4, 0, 4)
    vb.setSpacing(4)

    # ── Name row ──
    name_row = QWidget()
    name_row.setStyleSheet("background:transparent;")
    nh = QHBoxLayout(name_row)
    nh.setContentsMargins(0, 0, 0, 0)
    nh.setSpacing(6)
    nh.addStretch()

    name_edit = _ProfileNameEdit()
    name_edit.setFixedWidth(200)
    name_edit.setFixedHeight(24)
    name_edit.setPlaceholderText("Profile name…")
    name_edit.setText(profile_name_text)
    name_edit.setStyleSheet(
        "QLineEdit {"
        f"  background:{CLR_SURFACE_SCORE_AREA}; color:{CLR_STATE_SELECTED_FG};"
        f"  border:1px solid {CLR_INTERACTIVE_BDR}; border-radius:4px;"
        "  padding:0 6px; font-size:11px;"
        "}"
        f"QLineEdit:focus {{ border-color:{CLR_INTERACTIVE}; }}"
    )
    name_edit.textChanged.connect(on_name_changed)
    nh.addWidget(name_edit)

    sel_arrow_lbl = QLabel("➜")
    sel_arrow_lbl.setStyleSheet(f"color:{CLR_TEXT_LABEL_GROUP}; font-size:18px;")
    sel_arrow_lbl.setVisible(False)
    nh.addWidget(sel_arrow_lbl)

    sel_name_lbl = QLabel()
    sel_name_lbl.setFixedWidth(160)
    sel_name_lbl.setStyleSheet(
        f"color:{CLR_TEXT_LABEL_COUNT}; background:{CLR_SURFACE_SCORE_AREA}; border:1px solid {CLR_SURFACE_SEPARATOR};"
        " border-radius:4px; padding:0 6px; font-size:11px;"
    )
    sel_name_lbl.setVisible(False)
    nh.addWidget(sel_name_lbl)

    nh.addSpacing(16)

    chk_traits_only = QCheckBox("Only Trait Desirability")
    chk_traits_only.setChecked(profile_traits_only)
    chk_traits_only.setToolTip(
        "When checked, Save only stores Trait Desirability ratings into the profile\n"
        "and Load only restores those ratings — weights and other settings are untouched."
    )
    chk_traits_only.setStyleSheet(checkbox_style(font_size=10, emphasize_checked=True))
    chk_traits_only.stateChanged.connect(on_traits_only_changed)
    nh.addWidget(chk_traits_only)

    nh.addStretch()
    vb.addWidget(name_row)

    # ── Button row ──
    btn_row_w = QWidget()
    btn_row_w.setStyleSheet("background:transparent;")
    hb = QHBoxLayout(btn_row_w)
    hb.setContentsMargins(16, 0, 16, 0)
    hb.setSpacing(0)
    hb.addStretch()

    lbl = QLabel("PROFILES")
    lbl.setStyleSheet(
        f"color:{CLR_TEXT_LABEL_GROUP}; font-size:10px; font-weight:bold; letter-spacing:2px;"
    )
    hb.addWidget(lbl)
    hb.addSpacing(12)

    profile_btns = {}
    for n in range(1, _NUM_PROFILES + 1):
        btn = QPushButton(str(n))
        btn.setFixedSize(44, 36)
        btn.clicked.connect(lambda _=False, n=n: on_btn_clicked(n))
        profile_btns[n] = btn
        hb.addWidget(btn)
        if n < _NUM_PROFILES:
            hb.addSpacing(4)

    hb.addSpacing(20)

    _act_style = (
        "QPushButton { background:#0e1a2e; color:#7799bb; border:1px solid #1a2a44;"
        "  border-radius:4px; padding:2px 12px; font-size:11px; }"
        "QPushButton:hover { background:#122236; color:#99bbdd; border-color:#2a4a6a; }"
    )
    load_btn = QPushButton("Load")
    load_btn.setFixedHeight(28)
    load_btn.setStyleSheet(_act_style)
    load_btn.clicked.connect(on_load)
    hb.addWidget(load_btn)
    hb.addSpacing(6)

    save_btn = QPushButton("Save")
    save_btn.setFixedHeight(28)
    save_btn.setStyleSheet(_act_style)
    save_btn.clicked.connect(on_save)
    hb.addWidget(save_btn)
    hb.addSpacing(6)

    _del_style = (
        "QPushButton { background:#1a0e0e; color:#885555; border:1px solid #3a1a1a;"
        "  border-radius:4px; padding:2px 10px; font-size:11px; }"
        "QPushButton:hover { background:#2a1212; color:#cc7777; border-color:#662222; }"
    )
    delete_btn = QPushButton("Delete")
    delete_btn.setFixedHeight(28)
    delete_btn.setStyleSheet(_del_style)
    delete_btn.setToolTip("Delete the selected profile slot, restoring it to empty")
    delete_btn.clicked.connect(on_delete)
    hb.addWidget(delete_btn)
    hb.addSpacing(16)

    loaded_lbl = QLabel()
    loaded_lbl.setStyleSheet(f"color:{CLR_TEXT_LABEL_COUNT}; font-size:11px;")
    loaded_lbl.setVisible(False)
    hb.addWidget(loaded_lbl)
    hb.addSpacing(8)

    dirty_lbl = QLabel("● Modified")
    dirty_lbl.setStyleSheet("color:#bb8822; font-size:11px;")
    dirty_lbl.setVisible(False)
    hb.addWidget(dirty_lbl)

    hb.addStretch()
    vb.addWidget(btn_row_w)

    widget_refs = {
        "name_edit": name_edit,
        "sel_arrow_lbl": sel_arrow_lbl,
        "sel_name_lbl": sel_name_lbl,
        "profile_btns": profile_btns,
        "load_btn": load_btn,
        "save_btn": save_btn,
        "delete_btn": delete_btn,
        "loaded_lbl": loaded_lbl,
        "dirty_lbl": dirty_lbl,
        "chk_traits_only": chk_traits_only,
    }
    return bar, widget_refs


def update_profile_bar(
    widget_refs,
    active,
    loaded,
    profiles,
    is_dirty,
):
    """Refresh profile button styles, name preview, and status indicators.

    Args:
        widget_refs: Dict of widget references from build_profile_bar().
        active: Currently-selected profile slot number.
        loaded: Currently-loaded profile slot number.
        profiles: Dict of {slot_num: profile_data}.
        is_dirty: Whether current state differs from snapshot.
    """
    profile_btns = widget_refs["profile_btns"]
    for n, btn in profile_btns.items():
        sel   = (n == active)
        ld    = (n == loaded)
        has   = (n in profiles)
        if sel and ld:
            style = (
                f"background:{CLR_STATE_SELECTED_BG}; color:{CLR_STATE_SELECTED_FG};"
                f" border:2px solid {CLR_STATE_SELECTED_BORDER};"
            )
        elif sel and has:
            style = f"background:{CLR_SURFACE_SCORE_AREA}; color:{CLR_TEXT_LABEL_COUNT}; border:2px solid #3355aa;"
        elif sel:
            style = f"background:{CLR_SURFACE_APP_MAIN}; color:{CLR_TEXT_LABEL_GROUP}; border:2px dashed #1e2d55;"
        elif ld:
            style = f"background:{CLR_SURFACE_PANEL}; color:#5a9a88; border:2px solid #1a4a44;"
        elif has:
            style = f"background:{CLR_SURFACE_PANEL}; color:{CLR_TEXT_LABEL_GROUP}; border:1px solid #22224a;"
        else:
            style = f"background:{CLR_SURFACE_SCORE_AREA}; color:{CLR_TEXT_LABEL_GROUP}; border:1px dashed #141428;"
        btn.setStyleSheet(
            f"QPushButton {{ {style} border-radius:6px; font-size:22px; font-weight:bold; }}"
            f"QPushButton:hover {{ color:#aaaaee; border-color:#4444aa; }}"
        )
    # Name preview when active != loaded
    if active != loaded:
        sel_name = profiles.get(active, {}).get("name", "") or ""
        widget_refs["sel_name_lbl"].setText(sel_name or f"Profile {active}")
        widget_refs["sel_name_lbl"].setVisible(True)
        widget_refs["sel_arrow_lbl"].setVisible(True)
    else:
        widget_refs["sel_name_lbl"].setVisible(False)
        widget_refs["sel_arrow_lbl"].setVisible(False)
    if active != loaded:
        widget_refs["loaded_lbl"].setText(f"Loaded: {loaded}  -  Load or Save to sync")
        widget_refs["loaded_lbl"].setVisible(True)
    else:
        widget_refs["loaded_lbl"].setVisible(False)
    widget_refs["dirty_lbl"].setVisible(is_dirty)


def handle_profile_load(
    parent,
    active,
    profiles,
    profile_traits_only,
    is_dirty,
):
    """Show confirmation dialog for profile load.

    Returns:
        None if cancelled, or the profile data dict if confirmed.
    """
    n = active
    profile_data = profiles.get(n)
    if profile_data is None:
        QMessageBox.information(
            parent, "Empty Profile",
            f"Profile {n} has no saved settings yet.\n\nUse Save to store current settings here.",
            QMessageBox.Ok,
        )
        return None
    _TD = "<span style='color:#d8c050; font-weight:bold;'>Trait Desirability</span>"
    if profile_traits_only:
        msg = (f"Load Profile {n}?<br><br>"
               f"Only {_TD} ratings will be loaded.<br>"
               f"Weights and other settings will not change.")
        if is_dirty:
            msg += "<br><br>Unsaved changes to the current profile will be lost."
    else:
        msg = f"Load Profile {n}?\n\nYour current settings will be replaced with those saved in Profile {n}."
        if is_dirty:
            msg += "\n\nUnsaved changes to the current profile will be lost."
    dlg = _ConfirmDialog("Load Profile", msg, f"Load Profile {n}", parent=parent)
    if dlg.exec() != QDialog.Accepted:
        return None
    return profile_data


def handle_profile_save(
    parent,
    active,
    profiles,
    profile_traits_only,
    ma_ratings,
    serialize_fn,
):
    """Show confirmation dialog for profile save.

    Returns:
        None if cancelled, or the snapshot dict to store.
    """
    n = active
    has_data = n in profiles
    if profile_traits_only and not has_data:
        QMessageBox.warning(
            parent, "Profile Empty",
            f"Profile {n} has no saved settings yet.\n\n"
            f"\"Only Trait Desirability\" mode can only update an existing profile.\n\n"
            f"To proceed: uncheck \"Only Trait Desirability\", save a full profile to "
            f"slot {n}, then re-enable the option for future trait-only saves.",
            QMessageBox.Ok,
        )
        return None
    _TD = "<span style='color:#d8c050; font-weight:bold;'>Trait Desirability</span>"
    if profile_traits_only:
        msg = (f"Save to Profile {n}?<br><br>"
               f"Only {_TD} ratings will be updated.<br>"
               f"Weights and other settings will not be changed.")
    elif has_data:
        msg = f"Save to Profile {n}?\n\nThis will overwrite Profile {n} with your current settings."
    else:
        msg = f"Save to Profile {n}?\n\nProfile {n} is currently empty. Your settings will be saved here."
    dlg = _ConfirmDialog("Save Profile", msg, f"Save to Profile {n}", parent=parent)
    if dlg.exec() != QDialog.Accepted:
        return None
    if profile_traits_only:
        existing = dict(profiles.get(n, {}))
        existing["ma_ratings"] = dict(ma_ratings)
        return existing
    else:
        return serialize_fn()


def handle_profile_delete(parent, active, profiles):
    """Show confirmation dialog for profile delete.

    Returns:
        None if cancelled, or the slot number to delete.
    """
    n = active
    if n not in profiles:
        QMessageBox.information(
            parent, "Nothing to Delete",
            f"Profile {n} is already empty — there is nothing to delete.",
            QMessageBox.Ok,
        )
        return None
    if len(profiles) <= 1:
        QMessageBox.warning(
            parent, "Cannot Delete",
            "You cannot delete the only remaining saved profile.\n\n"
            "Save your settings to another slot first, then delete this one.",
            QMessageBox.Ok,
        )
        return None
    pname = profiles[n].get("name", "") or f"Profile {n}"
    dlg = _ConfirmDialog(
        "Delete Profile",
        f"Delete Profile {n} (\"{pname}\")?\n\n"
        f"This will erase all settings saved in slot {n} and cannot be undone.",
        f"Delete Profile {n}",
        parent=parent,
    )
    if dlg.exec() != QDialog.Accepted:
        return None
    return n
