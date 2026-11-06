"""Reusable widgets: rich log view, diff view and an auto-generated pydantic form."""

from __future__ import annotations

import json
import types
from typing import Any, Callable, Literal, Optional, Union, get_args, get_origin

from pydantic import BaseModel
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QTextEdit,
    QWidget,
)

COLORS = {
    "user": "#4a90d9",
    "tool": "#b07d2b",
    "muted": "#8a8a8a",
    "error": "#d9534f",
    "warn": "#e08a1e",
    "ok": "#3c9a5f",
    "add": "#3c9a5f",
    "del": "#d9534f",
    "hunk": "#3aa0b5",
}


def mono_font() -> QFont:
    f = QFont("monospace")
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f


class RichLog(QTextEdit):
    """Read-only append-only log with coloured spans; sticks to the bottom while following."""

    def __init__(self, mono: bool = False) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        if mono:
            self.setFont(mono_font())

    def write(self, text: str, color: Optional[str] = None, bold: bool = False, italic: bool = False,
              mono: bool = False) -> None:
        fmt = QTextCharFormat()
        if color:
            fmt.setForeground(QColor(COLORS.get(color, color)))
        if bold:
            fmt.setFontWeight(QFont.Weight.Bold)
        fmt.setFontItalic(italic)
        if mono:
            fmt.setFontFamilies(["monospace"])
        sb = self.verticalScrollBar()
        follow = sb.value() >= sb.maximum() - 6
        cur = QTextCursor(self.document())
        cur.movePosition(QTextCursor.MoveOperation.End)
        cur.insertText(text, fmt)
        if follow:
            sb.setValue(sb.maximum())

    def line(self, text: str = "", **kw: Any) -> None:
        self.write(text + "\n", **kw)


class DiffView(RichLog):
    def __init__(self) -> None:
        super().__init__(mono=True)

    def show_text(self, text: str) -> None:
        self.clear()
        for ln in text.splitlines():
            if ln.startswith("+") and not ln.startswith("+++"):
                self.line(ln, color="add", mono=True)
            elif ln.startswith("-") and not ln.startswith("---"):
                self.line(ln, color="del", mono=True)
            elif ln.startswith("@@"):
                self.line(ln, color="hunk", mono=True)
            else:
                self.line(ln, mono=True)
        self.verticalScrollBar().setValue(0)


# ------------------------------------------------------------- model form
def _unwrap(ann: Any) -> tuple[Any, bool]:
    """Strip Optional[...] -> (inner annotation, is_optional)."""
    if get_origin(ann) in (Union, types.UnionType):
        args = [a for a in get_args(ann) if a is not type(None)]
        if len(args) == 1:
            return args[0], len(args) < len(get_args(ann))
    return ann, False


Getter = Callable[[], Any]


def _make_field(ann: Any, value: Any, choices: Optional[list[str]]) -> tuple[QWidget, Getter]:
    inner, optional = _unwrap(ann)
    origin = get_origin(inner)

    if choices or origin is Literal:
        opts = choices or [str(a) for a in get_args(inner)]
        w = QComboBox()
        w.addItems(opts)
        if value in opts:
            w.setCurrentText(value)
        return w, w.currentText
    if inner is bool:
        if optional:
            w = QComboBox()
            w.addItems(["(unset)", "true", "false"])
            w.setCurrentIndex(0 if value is None else (1 if value else 2))
            return w, lambda: {0: None, 1: True, 2: False}[w.currentIndex()]
        cb = QCheckBox()
        cb.setChecked(bool(value))
        return cb, cb.isChecked
    if inner is int and not optional:
        sp = QSpinBox()
        sp.setRange(0, 2_000_000_000)
        sp.setValue(int(value or 0))
        return sp, sp.value
    if inner is float and not optional:
        ds = QDoubleSpinBox()
        ds.setRange(0, 1e9)
        ds.setDecimals(3)
        ds.setValue(float(value or 0))
        return ds, ds.value
    if inner in (int, float):  # optional number: blank = None
        le = QLineEdit("" if value is None else str(value))
        le.setPlaceholderText("(unset)")
        conv = int if inner is int else float
        return le, lambda: conv(le.text()) if le.text().strip() else None
    if origin is dict:
        le = QLineEdit(json.dumps(value or {}, ensure_ascii=False))
        le.setPlaceholderText("JSON object")
        return le, lambda: json.loads(le.text() or "{}")
    if origin is list:
        le = QLineEdit(", ".join(value or []))
        le.setPlaceholderText("comma separated")
        return le, lambda: [x.strip() for x in le.text().split(",") if x.strip()]
    le = QLineEdit("" if value is None else str(value))
    return le, le.text


class ModelForm(QWidget):
    """Generates a form from a pydantic model (so new config fields appear automatically)."""

    def __init__(self, model: BaseModel, choices: Optional[dict[str, list[str]]] = None,
                 skip: Optional[set[str]] = None, descriptions: Optional[dict[str, str]] = None) -> None:
        super().__init__()
        self._model_cls = type(model)
        self._getters: dict[str, Getter] = {}
        self._widgets: dict[str, QWidget] = {}
        layout = QFormLayout(self)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for name, field in self._model_cls.model_fields.items():
            if skip and name in skip:
                continue
            w, getter = _make_field(field.annotation, getattr(model, name), (choices or {}).get(name))
            if descriptions and name in descriptions:
                w.setToolTip(descriptions[name])
            layout.addRow(name, w)
            self._getters[name] = getter
            self._widgets[name] = w

    def set_value(self, name: str, value: Any) -> None:
        """Programmatically change a field (numbers, text and combo boxes)."""
        w = self._widgets[name]
        if isinstance(w, (QSpinBox, QDoubleSpinBox)):
            w.setValue(value)
        elif isinstance(w, QLineEdit):
            w.setText(str(value))
        elif isinstance(w, QComboBox):
            w.setCurrentText(str(value))
        elif isinstance(w, QCheckBox):
            w.setChecked(bool(value))

    def values(self) -> dict[str, Any]:
        out = {}
        for name, g in self._getters.items():
            try:
                out[name] = g()
            except (ValueError, json.JSONDecodeError) as e:
                raise ValueError(f"{name}: {e}") from e
        return out
