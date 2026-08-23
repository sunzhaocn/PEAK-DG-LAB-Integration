import csv, json, os, sys, threading, time, shutil
from io import BytesIO
from pathlib import Path

import qrcode
from PIL import Image, ImageEnhance, ImageFilter

try:
    from PySide6.QtCore import Qt, QTimer, Signal, QRect
    from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
    from PySide6.QtWidgets import (
        QApplication, QAbstractItemView, QCheckBox, QColorDialog, QComboBox,
        QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
        QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
        QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QProgressBar,
        QScrollArea, QSlider, QSpinBox, QSplitter, QStackedLayout, QStackedWidget,
        QSizePolicy, QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
    )
except Exception as e:
    raise SystemExit("缺少 PySide6，请先执行：pip install PySide6") from e

import backend as B
import i18n as I18N

# ============================================================
# Qt 外观设置。规则、设备协议、PEAK 监听全部继续使用 backend.py。
# ============================================================
APPEARANCE_DEFAULTS = {
    "background_enabled": True,
    "background_image": "",
    "background_opacity": 0.92,
    "background_blur": 6,
    "background_brightness": 0.68,
    "background_fit": "cover",
    "glass_opacity": 0.58,
    "glass_radius": 16,
    "glass_border_opacity": 0.28,
    "accent": "#5B8CFF",
    "show_values": True,
    "show_progress": True,
    "status_decimals": 1,
    "compact_status_cards": False,
}
appearance = dict(APPEARANCE_DEFAULTS)
ASSET_DIR = B.ROOT / "assets" / "background"
SAVED_BACKGROUND = ASSET_DIR / "wallpaper.jpg"


def load_full_config():
    B.load_config()
    I18N.reload_locales()
    I18N.set_language(
        I18N.DEFAULT_LANGUAGE
    )

    if not B.CONFIG_FILE.exists():
        return
    try:
        data = json.loads(B.CONFIG_FILE.read_text(encoding="utf-8"))
        I18N.set_language(
            data.get(
                "language",
                I18N.DEFAULT_LANGUAGE,
            )
        )

        loaded = data.get("appearance") or {}
        for k, v in loaded.items():
            if k in appearance:
                appearance[k] = v
    except Exception:
        pass
    appearance["background_opacity"] = max(0.0, min(1.0, float(appearance.get("background_opacity", .92))))
    appearance["background_blur"] = max(0, min(40, int(appearance.get("background_blur", 6))))
    appearance["background_brightness"] = max(.15, min(1.5, float(appearance.get("background_brightness", .68))))
    appearance["glass_opacity"] = max(.15, min(.95, float(appearance.get("glass_opacity", .58))))
    appearance["glass_radius"] = max(0, min(32, int(appearance.get("glass_radius", 16))))
    appearance["glass_border_opacity"] = max(0.0, min(1.0, float(appearance.get("glass_border_opacity", .28))))
    appearance["status_decimals"] = max(0, min(2, int(appearance.get("status_decimals", 1))))
    for k in ("background_enabled", "show_values", "show_progress", "compact_status_cards"):
        appearance[k] = bool(appearance.get(k, APPEARANCE_DEFAULTS[k]))

    # 背景持久化兜底：
    # 选择背景时会复制到项目 assets/background/wallpaper.jpg。
    # 即使配置文件中的旧路径丢失，只要保存的 wallpaper.png 还在，
    # 下次启动也自动恢复为默认背景。
    configured = resolve_background()

    if (
        (configured is None or not configured.exists())
        and SAVED_BACKGROUND.exists()
    ):
        appearance["background_image"] = rel_root(SAVED_BACKGROUND)
        appearance["background_enabled"] = True


def save_full_config():
    with B.rule_lock:
        rules = json.loads(json.dumps(B.rules, ensure_ascii=False))
    data = {
        "rules": rules,
        "network": {
            "peak_port": int(B.network_settings.get("peak_port", B.DEFAULT_PEAK_PORT)),
            "dg_port": int(B.network_settings.get("dg_port", B.DEFAULT_DG_PORT)),
            "peak_game_dir": str(B.network_settings.get("peak_game_dir", "") or ""),
        },
        "custom_waveforms": json.loads(json.dumps(B.custom_waveforms, ensure_ascii=False)),
        "appearance": dict(appearance),
        "language": I18N.get_language(),
    }
    try:
        B.CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True, "配置已保存"
    except Exception as e:
        return False, str(e)


def resolve_background():
    raw = str(appearance.get("background_image") or "")
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = B.ROOT / p
    return p


def rel_root(p):
    try:
        return str(Path(p).resolve().relative_to(B.ROOT.resolve()))
    except Exception:
        return str(Path(p).resolve())


def rgb(hex_color):
    c = QColor(str(hex_color))
    if not c.isValid():
        c = QColor("#5B8CFF")
    return c.red(), c.green(), c.blue()


# ============================================================
# 整个窗口的背景：真正位于所有 Qt 内容下面。
# ============================================================
class BackgroundWidget(QWidget):
    """
    High-performance background layer.

    Main optimization:
    - PIL prepares a complete viewport-sized background in a worker thread.
    - blur / brightness / fit / opacity are baked into that image.
    - paintEvent normally performs only one non-scaled drawPixmap().
    - during interactive resize, the previous pixmap may be stretched cheaply;
      a new exact-size cache is generated only after resize debounce.
    """

    image_ready = Signal(int, bytes, int, int)

    MAX_RENDER_W = 2048
    MAX_RENDER_H = 1400

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )

        self.setAutoFillBackground(
            False
        )

        self._cache_key = None
        self._pixmap = None

        self._request_id = 0
        self._worker_running = False
        self._pending_rebuild = False

        # Source image cache avoids decoding wallpaper.jpg again every time
        # the user changes blur / brightness / opacity.
        self._source_cache_path = None
        self._source_cache_image = None

        self.image_ready.connect(
            self._apply_worker_result
        )

    def invalidate(self):
        self._cache_key = None
        self._request_id += 1

        if self._worker_running:
            self._pending_rebuild = True
        else:
            self._start_worker()

        self.update()

    def fast_update(self):
        # Cheap paint only. No image processing is started here.
        self.update()

    def _render_size(self):
        width = max(
            320,
            int(self.width()),
        )

        height = max(
            240,
            int(self.height()),
        )

        scale = min(
            1.0,
            self.MAX_RENDER_W
            / max(1, width),
            self.MAX_RENDER_H
            / max(1, height),
        )

        return (
            max(
                320,
                int(width * scale),
            ),
            max(
                240,
                int(height * scale),
            ),
        )

    def _target_key(self):
        path = resolve_background()

        if (
            not appearance.get(
                "background_enabled",
                True,
            )
            or path is None
            or not path.exists()
        ):
            return None

        width, height = (
            self._render_size()
        )

        return (
            str(
                path.resolve()
            ),
            int(
                appearance.get(
                    "background_blur",
                    6,
                )
            ),
            round(
                float(
                    appearance.get(
                        "background_brightness",
                        .68,
                    )
                ),
                3,
            ),
            round(
                float(
                    appearance.get(
                        "background_opacity",
                        .92,
                    )
                ),
                3,
            ),
            str(
                appearance.get(
                    "background_fit",
                    "cover",
                )
            ),
            width,
            height,
        )

    @staticmethod
    def _fit_image(
        source,
        width,
        height,
        mode,
    ):
        source_w = max(
            1,
            source.width,
        )

        source_h = max(
            1,
            source.height,
        )

        if mode == "stretch":
            return source.resize(
                (
                    width,
                    height,
                ),
                Image.Resampling.BILINEAR,
            )

        canvas = Image.new(
            "RGB",
            (
                width,
                height,
            ),
            (
                8,
                12,
                18,
            ),
        )

        if mode == "contain":
            scale = min(
                width / source_w,
                height / source_h,
            )

            new_w = max(
                1,
                int(
                    source_w * scale
                ),
            )

            new_h = max(
                1,
                int(
                    source_h * scale
                ),
            )

            fitted = source.resize(
                (
                    new_w,
                    new_h,
                ),
                Image.Resampling.BILINEAR,
            )

            canvas.paste(
                fitted,
                (
                    (width - new_w)
                    // 2,
                    (height - new_h)
                    // 2,
                ),
            )

            return canvas

        # cover
        scale = max(
            width / source_w,
            height / source_h,
        )

        new_w = max(
            width,
            int(
                source_w * scale
            ),
        )

        new_h = max(
            height,
            int(
                source_h * scale
            ),
        )

        fitted = source.resize(
            (
                new_w,
                new_h,
            ),
            Image.Resampling.BILINEAR,
        )

        left = max(
            0,
            (new_w - width)
            // 2,
        )

        top = max(
            0,
            (new_h - height)
            // 2,
        )

        return fitted.crop(
            (
                left,
                top,
                left + width,
                top + height,
            )
        )

    def _start_worker(self):
        key = self._target_key()

        if key is None:
            self._pixmap = None
            self._cache_key = None
            self.update()
            return

        if (
            key == self._cache_key
            and self._pixmap is not None
        ):
            return

        if self._worker_running:
            self._pending_rebuild = True
            return

        self._worker_running = True
        self._pending_rebuild = False

        request_id = (
            self._request_id
        )

        (
            path_s,
            blur,
            brightness,
            opacity,
            fit_mode,
            width,
            height,
        ) = key

        def worker():
            payload = b""

            try:
                if (
                    self._source_cache_path
                    != path_s
                    or self._source_cache_image
                    is None
                ):
                    with Image.open(
                        path_s
                    ) as opened:
                        source = (
                            opened
                            .convert(
                                "RGB"
                            )
                        )

                    # Background import already limits images, but this protects
                    # manually replaced files as well.
                    if max(
                        source.size
                    ) > 2800:
                        source.thumbnail(
                            (
                                2800,
                                2800,
                            ),
                            Image.Resampling.BILINEAR,
                        )

                    self._source_cache_path = (
                        path_s
                    )

                    self._source_cache_image = (
                        source.copy()
                    )

                source = (
                    self._source_cache_image
                    .copy()
                )

                rendered = (
                    self._fit_image(
                        source,
                        width,
                        height,
                        fit_mode,
                    )
                )

                if (
                    abs(
                        brightness
                        - 1.0
                    )
                    > 0.001
                ):
                    rendered = (
                        ImageEnhance
                        .Brightness(
                            rendered
                        )
                        .enhance(
                            brightness
                        )
                    )

                if blur > 0:
                    # Final image is already viewport sized, so a smaller
                    # radius gives the same perceived blur at much lower cost.
                    rendered = rendered.filter(
                        ImageFilter.GaussianBlur(
                            min(
                                14.0,
                                float(
                                    blur
                                )
                                * 0.34,
                            )
                        )
                    )

                if opacity < 0.999:
                    base = Image.new(
                        "RGB",
                        (
                            width,
                            height,
                        ),
                        (
                            8,
                            12,
                            18,
                        ),
                    )

                    rendered = (
                        Image.blend(
                            base,
                            rendered,
                            max(
                                0.0,
                                min(
                                    1.0,
                                    opacity,
                                ),
                            ),
                        )
                    )

                bio = BytesIO()

                # Encoding is done off the UI thread.
                rendered.save(
                    bio,
                    "JPEG",
                    quality=84,
                    optimize=False,
                    subsampling=1,
                )

                payload = (
                    bio.getvalue()
                )

            except Exception as e:
                B.add_log(
                    "错误",
                    "背景缓存生成失败",
                    repr(e),
                )

            self.image_ready.emit(
                request_id,
                payload,
                width,
                height,
            )

        threading.Thread(
            target=worker,
            name="CoyoteBackgroundWorker",
            daemon=True,
        ).start()

    def _apply_worker_result(
        self,
        request_id,
        payload,
        width,
        height,
    ):
        self._worker_running = False

        if (
            request_id
            == self._request_id
            and payload
        ):
            pixmap = QPixmap()

            if pixmap.loadFromData(
                payload
            ):
                self._pixmap = pixmap
                self._cache_key = (
                    self._target_key()
                )

        elif (
            request_id
            == self._request_id
            and not payload
        ):
            self._pixmap = None
            self._cache_key = None

        self.update()

        if self._pending_rebuild:
            self._pending_rebuild = False
            self._start_worker()

    def paintEvent(
        self,
        event,
    ):
        painter = QPainter(
            self
        )

        try:
            painter.fillRect(
                self.rect(),
                QColor(
                    "#080C12"
                ),
            )

            if not appearance.get(
                "background_enabled",
                True,
            ):
                return

            pixmap = self._pixmap

            if (
                pixmap is None
                or pixmap.isNull()
            ):
                if not self._worker_running:
                    self._start_worker()

                return

            rect = self.rect()

            # Normal steady-state path: one direct blit, no smooth scaling,
            # no per-frame opacity composition.
            if (
                pixmap.width()
                == rect.width()
                and pixmap.height()
                == rect.height()
            ):
                painter.drawPixmap(
                    0,
                    0,
                    pixmap,
                )
                return

            # Interactive resize fallback. The exact cache is regenerated only
            # after resize debounce, so dragging the window remains responsive.
            painter.drawPixmap(
                rect,
                pixmap,
            )

        finally:
            if painter.isActive():
                painter.end()



class StatusCard(QFrame):
    clicked = Signal(str)
    def __init__(self, key, title):
        super().__init__(); self.key = key; self._last = 0.0
        self.setObjectName("glassCard"); self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self); lay.setContentsMargins(12, 10, 12, 10); lay.setSpacing(7)
        top = QHBoxLayout(); self.name = QLabel(title); self.name.setObjectName("cardTitle")
        self.value = QLabel("0.0%"); self.value.setObjectName("statusValue"); top.addWidget(self.name); top.addStretch(); top.addWidget(self.value); lay.addLayout(top)
        self.bar = QProgressBar(); self.bar.setRange(0,1000); self.bar.setTextVisible(False); self.bar.setFixedHeight(7); lay.addWidget(self.bar)
        self.btn = QPushButton("打开电击调节"); self.btn.clicked.connect(lambda: self.clicked.emit(self.key)); lay.addWidget(self.btn)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton: self.clicked.emit(self.key)
        super().mousePressEvent(e)

    def set_value(self, v):
        v = max(
            0.0,
            min(
                100.0,
                float(v),
            ),
        )

        self._last = v

        decimals = int(
            appearance.get(
                "status_decimals",
                1,
            )
        )

        text = (
            f"{v:.{decimals}f}%"
        )

        if self.value.text() != text:
            self.value.setText(
                text
            )

        value_visible = bool(
            appearance.get(
                "show_values",
                True,
            )
        )

        if (
            self.value.isVisible()
            != value_visible
        ):
            self.value.setVisible(
                value_visible
            )

        progress = int(
            v * 10
        )

        if (
            self.bar.value()
            != progress
        ):
            self.bar.setValue(
                progress
            )

        progress_visible = bool(
            appearance.get(
                "show_progress",
                True,
            )
        )

        if (
            self.bar.isVisible()
            != progress_visible
        ):
            self.bar.setVisible(
                progress_visible
            )

        button_visible = (
            not bool(
                appearance.get(
                    "compact_status_cards",
                    False,
                )
            )
        )

        if (
            self.btn.isVisible()
            != button_visible
        ):
            self.btn.setVisible(
                button_visible
            )


class DurationSpinBox(QSpinBox):
    """
    自动规则持续时间：
      -1 = 条件成立时持续续播
      100..MAX = 有限毫秒数
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(
            B.DURATION_CONTINUOUS,
            B.GUI_DURATION_MAX_MS,
        )
        self.setSingleStep(100)
        self.setSuffix(" ms")
        self.setSpecialValueText(
            "-1（持续）"
        )

    def stepBy(self, steps):
        current = self.value()

        if steps > 0:
            if current == B.DURATION_CONTINUOUS:
                self.setValue(100)
                return

            self.setValue(
                min(
                    B.GUI_DURATION_MAX_MS,
                    current + 100 * steps,
                )
            )
            return

        if steps < 0:
            if current <= 100:
                self.setValue(
                    B.DURATION_CONTINUOUS
                )
                return

            self.setValue(
                max(
                    100,
                    current + 100 * steps,
                )
            )


class TierRow(QFrame):
    remove_requested = Signal(object)

    def __init__(self, tier=None):
        super().__init__()
        tier = tier or {
            "below": 50,
            "add_a": 1,
            "add_b": 1,
            "waveform_a": B.TIER_WAVEFORM_INHERIT,
            "waveform_b": B.TIER_WAVEFORM_INHERIT,
        }
        self.setObjectName("tierRow")
        g = QGridLayout(self)
        g.setContentsMargins(8, 8, 8, 8)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(7)

        self.below = QDoubleSpinBox()
        self.below.setRange(0, 100)
        self.below.setDecimals(1)
        self.below.setSuffix(" %")
        self.below.setValue(float(tier.get("below", 50)))

        self.add_a = QSpinBox()
        self.add_a.setRange(0, B.GUI_INTENSITY_MAX)
        self.add_a.setValue(B.clamp_int(tier.get("add_a", 1)))
        self.add_b = QSpinBox()
        self.add_b.setRange(0, B.GUI_INTENSITY_MAX)
        self.add_b.setValue(B.clamp_int(tier.get("add_b", 1)))

        self.wa = QComboBox()
        self.wb = QComboBox()
        self.refresh_waveforms()
        self.wa.setCurrentText(tier.get("waveform_a", B.TIER_WAVEFORM_INHERIT))
        self.wb.setCurrentText(tier.get("waveform_b", B.TIER_WAVEFORM_INHERIT))

        rm = QPushButton("删除档位")
        rm.setObjectName("dangerGhost")
        rm.clicked.connect(lambda: self.remove_requested.emit(self))

        # 三行布局，不会因为窗口变窄把波形下拉框挤没。
        g.addWidget(QLabel("当前值低于"), 0, 0)
        g.addWidget(self.below, 0, 1)
        g.addWidget(rm, 0, 3)

        g.addWidget(QLabel("A 额外强度 +"), 1, 0)
        g.addWidget(self.add_a, 1, 1)
        g.addWidget(QLabel("A 档位波形"), 1, 2)
        g.addWidget(self.wa, 1, 3)

        g.addWidget(QLabel("B 额外强度 +"), 2, 0)
        g.addWidget(self.add_b, 2, 1)
        g.addWidget(QLabel("B 档位波形"), 2, 2)
        g.addWidget(self.wb, 2, 3)
        g.setColumnStretch(3, 1)

    def refresh_waveforms(self):
        a = self.wa.currentText() if self.wa.count() else ""
        b = self.wb.currentText() if self.wb.count() else ""
        vals = B.tier_waveform_names()
        self.wa.clear(); self.wb.clear()
        self.wa.addItems(vals); self.wb.addItems(vals)
        if a in vals: self.wa.setCurrentText(a)
        if b in vals: self.wb.setCurrentText(b)

    def data(self):
        return {
            "below": self.below.value(),
            "add_a": self.add_a.value(),
            "add_b": self.add_b.value(),
            "waveform_a": self.wa.currentText(),
            "waveform_b": self.wb.currentText(),
        }


class SpikeTierRow(QFrame):
    remove_requested = Signal(object)

    def __init__(self, tier=None):
        super().__init__()
        tier = tier or {
            "delta": 50.0,
            "min_a": 5,
            "max_a": 5,
            "min_b": 5,
            "max_b": 5,
        }
        self.setObjectName("tierRow")
        g = QGridLayout(self)
        g.setContentsMargins(8, 8, 8, 8)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(7)

        self.delta = QDoubleSpinBox()
        self.delta.setRange(0.1, 100.0)
        self.delta.setDecimals(1)
        self.delta.setSuffix(" %")
        self.delta.setValue(float(tier.get("delta", 50.0)))

        self.min_a = QSpinBox(); self.max_a = QSpinBox()
        self.min_b = QSpinBox(); self.max_b = QSpinBox()
        for w in (self.min_a, self.max_a, self.min_b, self.max_b):
            w.setRange(0, B.GUI_INTENSITY_MAX)

        a0, a1 = B.normalize_intensity_range(
            tier.get("min_a", tier.get("add_a", 5)),
            tier.get("max_a", tier.get("add_a", 5)), 5, 5
        )
        b0, b1 = B.normalize_intensity_range(
            tier.get("min_b", tier.get("add_b", 5)),
            tier.get("max_b", tier.get("add_b", 5)), 5, 5
        )
        self.min_a.setValue(a0); self.max_a.setValue(a1)
        self.min_b.setValue(b0); self.max_b.setValue(b1)

        rm = QPushButton("删除档位")
        rm.setObjectName("dangerGhost")
        rm.clicked.connect(lambda: self.remove_requested.emit(self))

        g.addWidget(QLabel("单次变化至少"), 0, 0)
        g.addWidget(self.delta, 0, 1)
        g.addWidget(rm, 0, 4)

        g.addWidget(QLabel("A 额外随机 +"), 1, 0)
        g.addWidget(self.min_a, 1, 1)
        g.addWidget(QLabel("~"), 1, 2)
        g.addWidget(self.max_a, 1, 3)

        g.addWidget(QLabel("B 额外随机 +"), 2, 0)
        g.addWidget(self.min_b, 2, 1)
        g.addWidget(QLabel("~"), 2, 2)
        g.addWidget(self.max_b, 2, 3)
        g.setColumnStretch(4, 1)

    def data(self):
        return {
            "delta": self.delta.value(),
            "min_a": min(self.min_a.value(), self.max_a.value()),
            "max_a": max(self.min_a.value(), self.max_a.value()),
            "min_b": min(self.min_b.value(), self.max_b.value()),
            "max_b": max(self.min_b.value(), self.max_b.value()),
        }


class RuleEditor(QFrame):
    def __init__(self, key, display, trigger):
        super().__init__()
        self.key = key
        self.display = display
        self.trigger = trigger
        self.rows = []
        self.spike_rows = []
        self.setObjectName("glassPanel")

        out = QVBoxLayout(self)
        out.setContentsMargins(16, 14, 16, 16)
        out.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel(display)
        title.setStyleSheet("font-size:20px;font-weight:700")
        trigger_label = QLabel(trigger)
        trigger_label.setObjectName("muted")
        self.enabled = QCheckBox("启用此规则")
        head.addWidget(title)
        head.addWidget(trigger_label)
        head.addStretch(1)
        head.addWidget(self.enabled)
        out.addLayout(head)

        intro = QLabel("该页直接编辑当前规则。无需先返回总览，也不需要再点展开按钮。")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        out.addWidget(intro)

        self.ia = QSpinBox(); self.ib = QSpinBox()
        self.ma = QSpinBox(); self.mb = QSpinBox()
        for s in (self.ia, self.ib, self.ma, self.mb):
            s.setRange(0, B.GUI_INTENSITY_MAX)

        self.da = DurationSpinBox()
        self.db = DurationSpinBox()

        self.wa = QComboBox(); self.wb = QComboBox()
        self.cool = QDoubleSpinBox()
        self.cool.setRange(0, 60)
        self.cool.setSingleStep(.5)
        self.cool.setSuffix(" s")

        self.trigger_mode = QComboBox()
        self.trigger_mode.addItem(
            "单次",
            "single",
        )
        self.trigger_mode.addItem(
            "持续（按冷却重复）",
            "repeat",
        )

        # A/B 分成上下两块，比四列横排更适合小窗口。
        a_box = QGroupBox("A 通道")
        af = QFormLayout(a_box)
        af.addRow("基础强度", self.ia)
        af.addRow("最大强度", self.ma)
        af.addRow("持续时间", self.da)
        af.addRow("基础波形", self.wa)
        out.addWidget(a_box)

        b_box = QGroupBox("B 通道")
        bf = QFormLayout(b_box)
        bf.addRow("基础强度", self.ib)
        bf.addRow("最大强度", self.mb)
        bf.addRow("持续时间", self.db)
        bf.addRow("基础波形", self.wb)
        out.addWidget(b_box)

        random_box = QGroupBox("随机强度")
        rg = QGridLayout(random_box)
        self.random_enabled = QCheckBox("每次触发随机基础强度")
        self.random_min_a = QSpinBox(); self.random_max_a = QSpinBox()
        self.random_min_b = QSpinBox(); self.random_max_b = QSpinBox()
        for spin in (self.random_min_a, self.random_max_a, self.random_min_b, self.random_max_b):
            spin.setRange(0, B.GUI_INTENSITY_MAX)
        rg.addWidget(self.random_enabled, 0, 0, 1, 4)
        rg.addWidget(QLabel("A 范围"), 1, 0)
        rg.addWidget(self.random_min_a, 1, 1)
        rg.addWidget(QLabel("到"), 1, 2)
        rg.addWidget(self.random_max_a, 1, 3)
        rg.addWidget(QLabel("B 范围"), 2, 0)
        rg.addWidget(self.random_min_b, 2, 1)
        rg.addWidget(QLabel("到"), 2, 2)
        rg.addWidget(self.random_max_b, 2, 3)
        random_note = QLabel("开启后，每次触发先从所选范围随机基础强度，再叠加动态档位/瞬时加强；最终仍受本规则最大强度限制。")
        random_note.setObjectName("muted")
        random_note.setWordWrap(True)
        rg.addWidget(random_note, 3, 0, 1, 4)
        out.addWidget(random_box)

        common = QGroupBox("通用")
        cf = QFormLayout(common)

        cf.addRow(
            "触发方式",
            self.trigger_mode,
        )
        cf.addRow(
            "触发 / 重复冷却",
            self.cool,
        )

        mode_note = QLabel(
            "持续时间填 -1：该通道在规则条件仍成立时自动续播。"
            "底层仍拆成有限片段；条件解除、总输出关闭、断线或立即停止后不再续播。"
            "设备控制页也支持 -1，但必须通过“立即停止”或总输出开关结束手动持续会话。"
        )
        mode_note.setObjectName(
            "muted"
        )
        mode_note.setWordWrap(
            True
        )
        cf.addRow(
            "",
            mode_note,
        )

        self.trigger_delta = None

        if key == "staminaUse":
            self.trigger_delta = QDoubleSpinBox()
            self.trigger_delta.setRange(
                0.1,
                50.0,
            )
            self.trigger_delta.setDecimals(
                1
            )
            self.trigger_delta.setSingleStep(
                0.5
            )
            self.trigger_delta.setSuffix(
                " %"
            )
            cf.addRow(
                "单次至少下降",
                self.trigger_delta,
            )

            tip = QLabel(
                "体力每次至少下降到这个幅度才会触发；同时仍受规则冷却时间限制。"
            )
            tip.setObjectName("muted")
            tip.setWordWrap(True)
            cf.addRow("", tip)

        self.speed_threshold = None

        if key in (
            "speedBelow",
            "speedAbove",
        ):
            self.speed_threshold = QDoubleSpinBox()
            self.speed_threshold.setRange(
                0.0,
                1000.0,
            )
            self.speed_threshold.setDecimals(
                3
            )
            self.speed_threshold.setSingleStep(
                0.1
            )
            self.speed_threshold.setValue(
                1.0
                if key == "speedBelow"
                else 5.0
            )

            cf.addRow(
                "速度阈值",
                self.speed_threshold,
            )

            speed_tip = QLabel(
                (
                    "使用 PEAK 遥测中的 speed 数值。"
                    "仅在速度跨过阈值时触发一次，"
                    "不会因为持续低速/高速而每帧重复触发。"
                )
            )
            speed_tip.setObjectName(
                "muted"
            )
            speed_tip.setWordWrap(
                True
            )
            cf.addRow(
                "",
                speed_tip,
            )

        self.item_filter = None

        if key in (
            "heldItem",
            "backpackItem",
            "heldState",
            "backpackState",
        ):
            self.item_filter = QLineEdit()
            self.item_filter.setPlaceholderText(
                "留空=任意；例如 Marshmallow, Rope, Lantern"
            )
            cf.addRow("物品名称筛选", self.item_filter)
            item_tip = QLabel(
                "支持逗号 / 分号 / 竖线分隔，忽略大小写按名称包含匹配；* 也表示任意。"
            )
            item_tip.setObjectName("muted")
            item_tip.setWordWrap(True)
            cf.addRow("", item_tip)

        if key in (
            "hp",
            "staminaUse",
            "jump",
        ):
            instant_tip = QLabel(
                "此规则属于瞬时事件，没有可靠的持续条件。"
                "因此持续时间填 -1 时也只发送一个有限片段；"
                "事件再次发生才会再次触发。"
            )
            instant_tip.setObjectName(
                "muted"
            )
            instant_tip.setWordWrap(
                True
            )
            cf.addRow(
                "",
                instant_tip,
            )

        out.addWidget(common)

        self.spike_enabled = None
        self.spike_rows_layout = None

        if B.rule_supports_percentage_tiers(key):
            spike_box = QGroupBox("瞬时大变化加强（自定义多档）")
            sv = QVBoxLayout(spike_box)
            self.spike_enabled = QCheckBox("启用瞬时大变化额外加强")
            sv.addWidget(self.spike_enabled)
            spike_note = QLabel(
                "按单次变化量判断。例如血量 100%→80% 是变化 20%，100%→25% 是变化 75%。"
                "可以自行添加任意阈值和 A/B 额外随机范围；多档同时命中只取最高阈值一档，不累加。"
                "把范围两端设成相同数值就是固定额外强度。最终仍受本规则最大强度限制。"
            )
            spike_note.setObjectName("muted")
            spike_note.setWordWrap(True)
            sv.addWidget(spike_note)
            self.spike_rows_layout = QVBoxLayout()
            sv.addLayout(self.spike_rows_layout)
            add_spike = QPushButton("＋ 添加瞬时变化档位")
            add_spike.clicked.connect(self.add_spike_tier)
            sv.addWidget(add_spike, alignment=Qt.AlignmentFlag.AlignLeft)
            out.addWidget(spike_box)

        if B.rule_supports_percentage_tiers(key):
            box = QGroupBox("状态值降低时的动态强度 / 波形档位")
            tl = QVBoxLayout(box)
            note = QLabel("例如低于 50% 时 A +3 并切换波形。多档同时命中只取最严重一档，不累加。")
            note.setObjectName("muted")
            note.setWordWrap(True)
            tl.addWidget(note)
            self.rows_layout = QVBoxLayout()
            tl.addLayout(self.rows_layout)
            ab = QPushButton("＋ 添加一个档位")
            ab.clicked.connect(self.add_tier)
            tl.addWidget(ab, alignment=Qt.AlignmentFlag.AlignLeft)
            out.addWidget(box)
        else:
            if key == "heldItem":
                text = "事件型：手持物切换成匹配物品时触发一次。"
            elif key == "backpackItem":
                text = "事件型：背包中新增加匹配物品时触发一次；取出物品不会触发。"
            elif key == "heldState":
                text = (
                    "状态型：当前手持物从“不匹配”进入“匹配”时触发。"
                    "选择持续模式或持续时间 -1 后，只要仍拿着匹配物品即可继续续播。"
                )
            elif key == "backpackState":
                text = (
                    "状态型：背包从“没有匹配物品”进入“存在匹配物品”时触发。"
                    "选择持续模式或持续时间 -1 后，只要背包仍存在匹配物品即可继续续播。"
                )
            elif key == "speedBelow":
                text = "速度从阈值以上进入阈值以下时触发一次。"
            elif key == "speedAbove":
                text = "速度从阈值以下进入阈值以上时触发一次。"
            else:
                text = f"{display}是事件规则，仅在对应状态发生变化时触发。"
            note = QLabel(text)
            note.setObjectName("muted")
            note.setWordWrap(True)
            out.addWidget(note)

        out.addStretch(1)
        self.refresh_waveforms()

    # 兼容旧调用；新界面不再折叠。
    def toggle(self, force=None):
        self.show()

    def refresh_waveforms(self):
        a = self.wa.currentText() if self.wa.count() else ""
        b = self.wb.currentText() if self.wb.count() else ""
        vals = B.waveform_names()
        self.wa.clear(); self.wb.clear()
        self.wa.addItems(vals); self.wb.addItems(vals)
        if a in vals: self.wa.setCurrentText(a)
        if b in vals: self.wb.setCurrentText(b)
        for r in self.rows:
            r.refresh_waveforms()

    def add_tier(self, tier=None):
        if not B.rule_supports_percentage_tiers(self.key):
            return
        r = TierRow(tier)
        r.remove_requested.connect(self.remove_tier)
        self.rows.append(r)
        self.rows_layout.addWidget(r)

    def remove_tier(self, r):
        if r in self.rows:
            self.rows.remove(r)
        r.setParent(None)
        r.deleteLater()

    def clear_tiers(self):
        for r in list(self.rows):
            self.remove_tier(r)

    def add_spike_tier(self, tier=None):
        if not B.rule_supports_percentage_tiers(self.key) or self.spike_rows_layout is None:
            return
        r = SpikeTierRow(tier)
        r.remove_requested.connect(self.remove_spike_tier)
        self.spike_rows.append(r)
        self.spike_rows_layout.addWidget(r)

    def remove_spike_tier(self, r):
        if r in self.spike_rows:
            self.spike_rows.remove(r)
        r.setParent(None)
        r.deleteLater()

    def clear_spike_tiers(self):
        for r in list(self.spike_rows):
            self.remove_spike_tier(r)

    def load_rule(self, c):
        self.enabled.setChecked(bool(c.get("enabled", False)))
        self.ia.setValue(B.clamp_int(c.get("intensity_a", 5)))
        self.ib.setValue(B.clamp_int(c.get("intensity_b", 5)))
        self.ma.setValue(B.clamp_int(c.get("max_intensity_a", 10)))
        self.mb.setValue(B.clamp_int(c.get("max_intensity_b", 10)))
        self.da.setValue(B.clamp_duration(c.get("play_time_a", 5000)))
        self.db.setValue(B.clamp_duration(c.get("play_time_b", 5000)))
        self.cool.setValue(B.clamp_cooldown(c.get("cooldown", 2)))

        self.random_enabled.setChecked(bool(c.get("random_intensity", False)))
        rmin_a, rmax_a = B.normalize_intensity_range(c.get("random_min_a", 1), c.get("random_max_a", 5), 1, 5)
        rmin_b, rmax_b = B.normalize_intensity_range(c.get("random_min_b", 1), c.get("random_max_b", 5), 1, 5)
        self.random_min_a.setValue(rmin_a); self.random_max_a.setValue(rmax_a)
        self.random_min_b.setValue(rmin_b); self.random_max_b.setValue(rmax_b)

        if self.spike_enabled is not None:
            self.spike_enabled.setChecked(bool(c.get("spike_enabled", False)))
            self.clear_spike_tiers()
            spike_tiers = B.normalize_spike_tiers(c.get("spike_tiers", []))
            # 自动兼容上一版单阈值配置：首次打开时转换成一个固定范围档位。
            if not spike_tiers and bool(c.get("spike_enabled", False)):
                try:
                    legacy_delta = max(0.1, min(100.0, float(c.get("spike_delta", 50.0))))
                except Exception:
                    legacy_delta = 50.0
                legacy_a = B.clamp_int(c.get("spike_add_a", 5))
                legacy_b = B.clamp_int(c.get("spike_add_b", 5))
                spike_tiers = [{
                    "delta": legacy_delta,
                    "min_a": legacy_a, "max_a": legacy_a,
                    "min_b": legacy_b, "max_b": legacy_b,
                }]
            for tier in spike_tiers:
                self.add_spike_tier(tier)

        mode = str(
            c.get(
                "trigger_mode",
                "single",
            )
            or "single"
        ).lower()

        mode_index = self.trigger_mode.findData(
            "repeat"
            if mode == "repeat"
            else "single"
        )

        if mode_index >= 0:
            self.trigger_mode.setCurrentIndex(
                mode_index
            )

        if self.trigger_delta is not None:
            try:
                self.trigger_delta.setValue(
                    max(
                        0.1,
                        float(
                            c.get(
                                "trigger_delta",
                                1.0,
                            )
                        ),
                    )
                )
            except Exception:
                self.trigger_delta.setValue(1.0)

        if self.speed_threshold is not None:
            try:
                self.speed_threshold.setValue(
                    max(
                        0.0,
                        float(
                            c.get(
                                "speed_threshold",
                                1.0
                                if self.key == "speedBelow"
                                else 5.0,
                            )
                        ),
                    )
                )
            except Exception:
                self.speed_threshold.setValue(
                    1.0
                    if self.key == "speedBelow"
                    else 5.0
                )

        if self.item_filter is not None:
            self.item_filter.setText(
                str(c.get("item_filter", "") or "")
            )

        self.refresh_waveforms()
        self.wa.setCurrentText(c.get("waveform_a", "脉冲"))
        self.wb.setCurrentText(c.get("waveform_b", "脉冲"))
        self.clear_tiers()
        if B.rule_supports_percentage_tiers(self.key):
            for tier in B.normalize_thresholds(c.get("thresholds", [])):
                self.add_tier(tier)

    def data(self):
        return {
            "enabled": self.enabled.isChecked(),
            "intensity_a": self.ia.value(),
            "intensity_b": self.ib.value(),
            "max_intensity_a": self.ma.value(),
            "max_intensity_b": self.mb.value(),
            "play_time_a": self.da.value(),
            "play_time_b": self.db.value(),
            "waveform_a": self.wa.currentText(),
            "waveform_b": self.wb.currentText(),
            "cooldown": self.cool.value(),
            "trigger_mode": (
                self.trigger_mode.currentData()
                or "single"
            ),
            "trigger_delta": (
                self.trigger_delta.value()
                if self.trigger_delta is not None
                else 1.0
            ),
            "item_filter": (
                self.item_filter.text().strip()
                if self.item_filter is not None
                else ""
            ),
            "speed_threshold": (
                self.speed_threshold.value()
                if self.speed_threshold is not None
                else (
                    1.0
                    if self.key == "speedBelow"
                    else 5.0
                )
            ),
            "random_intensity": self.random_enabled.isChecked(),
            "random_min_a": min(self.random_min_a.value(), self.random_max_a.value()),
            "random_max_a": max(self.random_min_a.value(), self.random_max_a.value()),
            "random_min_b": min(self.random_min_b.value(), self.random_max_b.value()),
            "random_max_b": max(self.random_min_b.value(), self.random_max_b.value()),
            "spike_enabled": self.spike_enabled.isChecked() if self.spike_enabled is not None else False,
            # 旧字段仅保留兼容；新版实际使用 spike_tiers。
            "spike_delta": 50.0,
            "spike_add_a": 0,
            "spike_add_b": 0,
            "spike_tiers": [r.data() for r in self.spike_rows] if B.rule_supports_percentage_tiers(self.key) else [],
            "thresholds": [r.data() for r in self.rows] if B.rule_supports_percentage_tiers(self.key) else [],
        }


class Window(QMainWindow):
    bepinex_progress = Signal(str)
    bepinex_finished = Signal(bool, str, object)
    background_import_finished = Signal(bool, str, str)
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Coyote / PEAK Controller")
        self.resize(1480, 900)
        self.setMinimumSize(760, 560)

        self.status_cards = {}
        self.status_card_order = []
        self.rule_editors = {}
        self.rule_list_items = {}
        self.nav_buttons = []
        self.page_indices = {}
        self.last_log_count = -1
        self.last_log_revision = -1
        self.qr_url = None
        self.selected_wave = None
        self._sidebar_collapsed = False
        self._last_status_columns = None

        # Appearance sliders enter a lightweight interaction mode:
        # telemetry/UI refresh is temporarily skipped while dragging.
        self._appearance_dragging = False

        self.build_root()
        self.build_ui()
        self.apply_theme()
        self.load_rules()
        self.refresh_wave_lists()
        self.retranslate_ui()

        self.stack.setCurrentWidget(self.content)
        self.bg.lower()
        self.content.raise_()

        self.statusBar().setStyleSheet("""
            QStatusBar {
                background: rgba(8,13,21,220);
                color: #DCE7F5;
                border-top: 1px solid rgba(210,225,245,55);
                padding: 3px 8px;
            }
        """)
        self.feedback("控制器已启动。等待 PEAK / DG-LAB 连接……", 5000)

        self.bepinex_progress.connect(
            self.on_bepinex_progress
        )
        self.bepinex_finished.connect(
            self.on_bepinex_finished
        )
        self.background_import_finished.connect(
            self.on_background_import_finished
        )

        self._background_refresh_timer = QTimer(self)
        self._background_refresh_timer.setSingleShot(True)
        self._background_refresh_timer.setInterval(80)
        self._background_refresh_timer.timeout.connect(self.refresh_background_cache)

        # Resize work used to rebuild waveform cards and layouts on every
        # Windows resize event. Do it once after the user pauses resizing.
        self._resize_refresh_timer = QTimer(
            self
        )
        self._resize_refresh_timer.setSingleShot(
            True
        )
        self._resize_refresh_timer.setInterval(
            220
        )
        self._resize_refresh_timer.timeout.connect(
            self._finish_resize_refresh
        )

        # GUI remains 5 Hz normally. PEAK rule evaluation stays in backend
        # UDP thread and is not affected by this timer.
        self.timer = QTimer(
            self
        )
        self.timer.timeout.connect(
            self.refresh_ui
        )
        self.timer.start(
            200
        )

        QTimer.singleShot(
            80,
            self.reflow_status_cards,
        )
        QTimer.singleShot(300, self.refresh_bepinex_status)

    def feedback(
        self,
        text,
        timeout=3500,
    ):
        """窗口底部显示操作反馈，不打断当前操作。"""
        self.statusBar().showMessage(
            I18N.tr_dynamic(
                str(text)
            ),
            int(timeout),
        )

    def _message_box(
        self,
        title,
        text,
        *,
        kind="info",
        detail="",
        confirm=False,
        confirm_text="确认",
        cancel_text="取消",
    ):
        """
        统一弹窗。

        旧版全局 QWidget 样式把 QMessageBox 的文字也设成白色，
        但系统消息框局部可能仍是浅色背景，结果看起来像“没有文字”。
        这里给消息框明确设置深色背景、白色正文和按钮文字。
        """
        box = QMessageBox(self)

        box.setWindowTitle(
            I18N.tr_dynamic(
                str(title)
            )
        )

        box.setText(
            I18N.tr_dynamic(
                str(text)
            )
        )

        if detail:
            box.setInformativeText(
                I18N.tr_dynamic(
                    str(detail)
                )
            )

        icons = {
            "info": QMessageBox.Icon.Information,
            "warning": QMessageBox.Icon.Warning,
            "error": QMessageBox.Icon.Critical,
            "question": QMessageBox.Icon.Question,
        }

        box.setIcon(
            icons.get(
                kind,
                QMessageBox.Icon.Information,
            )
        )

        box.setStyleSheet(
            """
            QMessageBox {
                background-color: #111827;
            }

            QMessageBox QLabel {
                color: #EAF0FA;
                min-width: 360px;
                font-family: "Microsoft YaHei UI", "Segoe UI";
                font-size: 13px;
            }

            QMessageBox QPushButton {
                min-width: 88px;
                min-height: 30px;
                padding: 5px 14px;
                color: #F8FAFC;
                background-color: #243247;
                border: 1px solid #45566F;
                border-radius: 7px;
            }

            QMessageBox QPushButton:hover {
                background-color: #31558E;
                border-color: #5B8CFF;
            }
            """
        )

        if confirm:
            yes_button = box.addButton(
                I18N.tr(
                    str(confirm_text)
                ),
                QMessageBox.ButtonRole.AcceptRole,
            )

            no_button = box.addButton(
                I18N.tr(
                    str(cancel_text)
                ),
                QMessageBox.ButtonRole.RejectRole,
            )

            box.setDefaultButton(
                no_button
            )

            box.exec()

            result = (
                box.clickedButton()
                is yes_button
            )

            if not result:
                self.feedback(
                    f"已取消：{title}",
                    2500,
                )

            return result

        box.addButton(
            I18N.tr("确定"),
            QMessageBox.ButtonRole.AcceptRole,
        )

        box.exec()
        return True

    def msg_info(
        self,
        title,
        text,
        detail="",
    ):
        self.feedback(
            text,
            3500,
        )

        return self._message_box(
            title,
            text,
            kind="info",
            detail=detail,
        )

    def msg_warning(
        self,
        title,
        text,
        detail="",
    ):
        self.feedback(
            text,
            5000,
        )

        return self._message_box(
            title,
            text,
            kind="warning",
            detail=detail,
        )

    def msg_error(
        self,
        title,
        text,
        detail="",
    ):
        self.feedback(
            f"错误：{text}",
            7000,
        )

        return self._message_box(
            title,
            text,
            kind="error",
            detail=detail,
        )

    def ask_confirm(
        self,
        title,
        text,
        detail="",
        confirm_text="确认",
        cancel_text="取消",
    ):
        return self._message_box(
            title,
            text,
            kind="question",
            detail=detail,
            confirm=True,
            confirm_text=confirm_text,
            cancel_text=cancel_text,
        )

    # --------------------------------------------------------
    # 多语言 / i18n
    # --------------------------------------------------------

    I18N_ROLE = int(
        Qt.ItemDataRole.UserRole
    ) + 97

    def _translate_table_headers(
        self,
        table,
    ):
        for column in range(
            table.columnCount()
        ):
            item = table.horizontalHeaderItem(
                column
            )

            if item is None:
                continue

            source = item.data(
                self.I18N_ROLE
            )

            if not source:
                source = item.text()
                item.setData(
                    self.I18N_ROLE,
                    source,
                )

            item.setText(
                I18N.tr(
                    source
                )
            )

        for row in range(
            table.rowCount()
        ):
            item = table.verticalHeaderItem(
                row
            )

            if item is None:
                continue

            source = item.data(
                self.I18N_ROLE
            )

            if not source:
                source = item.text()
                item.setData(
                    self.I18N_ROLE,
                    source,
                )

            item.setText(
                I18N.tr(
                    source
                )
            )

    def _translate_combo(
        self,
        combo,
    ):
        if combo.property(
            "i18n_skip"
        ):
            return

        current_data = combo.currentData()
        current_index = combo.currentIndex()

        for index in range(
            combo.count()
        ):
            source = combo.itemData(
                index,
                self.I18N_ROLE,
            )

            if not source:
                source = combo.itemText(
                    index
                )

                combo.setItemData(
                    index,
                    source,
                    self.I18N_ROLE,
                )

            combo.setItemText(
                index,
                I18N.tr(
                    source
                ),
            )

        # Preserve semantic item data/selection.
        if current_data is not None:
            matched = combo.findData(
                current_data
            )

            if matched >= 0:
                combo.setCurrentIndex(
                    matched
                )
                return

        if (
            current_index >= 0
            and current_index
            < combo.count()
        ):
            combo.setCurrentIndex(
                current_index
            )

    def _i18n_source(
        self,
        obj,
        property_name,
        current_text,
    ):
        """
        Every static widget permanently stores its canonical source text.

        Never translate the already-translated visible text again.
        """
        source = obj.property(
            property_name
        )

        if source is None or source == "":
            source = str(
                current_text
                or ""
            )
            obj.setProperty(
                property_name,
                source,
            )

        return str(source)

    def retranslate_ui(self):
        """
        Static UI uses canonical source keys.
        Runtime logs/telemetry continue to use tr_dynamic().
        """
        self.setWindowTitle(
            I18N.tr(
                "Coyote / PEAK Controller"
            )
        )

        for widget in self.findChildren(
            QWidget
        ):
            if widget.property(
                "i18n_skip"
            ):
                continue

            if isinstance(
                widget,
                QGroupBox,
            ):
                source = self._i18n_source(
                    widget,
                    "i18n_title_source",
                    widget.title(),
                )

                widget.setTitle(
                    I18N.tr(
                        source
                    )
                )

            if isinstance(
                widget,
                (
                    QLabel,
                    QPushButton,
                    QCheckBox,
                ),
            ):
                # Dynamic widgets are explicitly marked i18n_dynamic.
                if not widget.property(
                    "i18n_dynamic"
                ):
                    source = self._i18n_source(
                        widget,
                        "i18n_text_source",
                        widget.text(),
                    )

                    if source:
                        widget.setText(
                            I18N.tr(
                                source
                            )
                        )

            if isinstance(
                widget,
                QLineEdit,
            ):
                source = self._i18n_source(
                    widget,
                    "i18n_placeholder_source",
                    widget.placeholderText(),
                )

                if source:
                    widget.setPlaceholderText(
                        I18N.tr(
                            source
                        )
                    )

            if isinstance(
                widget,
                QComboBox,
            ):
                self._translate_combo(
                    widget
                )

            if isinstance(
                widget,
                QTableWidget,
            ):
                self._translate_table_headers(
                    widget
                )

            if isinstance(
                widget,
                QListWidget,
            ):
                for index in range(
                    widget.count()
                ):
                    item = widget.item(
                        index
                    )

                    source = item.data(
                        self.I18N_ROLE
                    )

                    if not source:
                        source = item.text()
                        item.setData(
                            self.I18N_ROLE,
                            source,
                        )

                    item.setText(
                        I18N.tr(
                            source
                        )
                    )

            if isinstance(
                widget,
                QTabWidget,
            ):
                tab_bar = widget.tabBar()

                for index in range(
                    widget.count()
                ):
                    source = tab_bar.tabData(
                        index
                    )

                    if not source:
                        source = widget.tabText(
                            index
                        )
                        tab_bar.setTabData(
                            index,
                            source,
                        )

                    widget.setTabText(
                        index,
                        I18N.tr(
                            source
                        ),
                    )

        # Sidebar is rendered from icon + canonical source label.
        for button in getattr(
            self,
            "nav_buttons",
            [],
        ):
            icon = str(
                button.property(
                    "nav_icon"
                )
                or ""
            )

            source_label = str(
                button.property(
                    "nav_source_label"
                )
                or ""
            )

            if not source_label:
                continue

            translated = I18N.tr(
                source_label
            )

            button.setProperty(
                "fullText",
                (
                    f"{icon}   {translated}"
                )
            )

            if not self._sidebar_collapsed:
                button.setText(
                    button.property(
                        "fullText"
                    )
                )

        # Static side subtitle.
        if hasattr(
            self,
            "side_subtitle",
        ):
            self.side_subtitle.setText(
                "PEAK × DG-LAB"
            )

        # Dynamic labels must be regenerated by refresh_ui().
        for dynamic_widget in (
            getattr(
                self,
                "side_peak",
                None,
            ),
            getattr(
                self,
                "side_dg",
                None,
            ),
            getattr(
                self,
                "header_connection",
                None,
            ),
        ):
            if dynamic_widget is not None:
                dynamic_widget.setProperty(
                    "i18n_dynamic",
                    True,
                )

        if hasattr(
            self,
            "page_stack",
        ):
            self.switch_page(
                self.page_stack.currentIndex()
            )

        if hasattr(
            self,
            "language_combo",
        ):
            code = I18N.get_language()
            index = self.language_combo.findData(
                code
            )

            if index >= 0:
                self.language_combo.blockSignals(
                    True
                )
                self.language_combo.setCurrentIndex(
                    index
                )
                self.language_combo.blockSignals(
                    False
                )

        self.last_log_count = -1
        self.last_log_revision = -1

        if hasattr(
            self,
            "logtable",
        ):
            self.refresh_logs()

        self.refresh_custom_rule_table()

        # Force dynamic labels to immediately adopt the selected language.
        if hasattr(
            self,
            "conn",
        ):
            self.refresh_ui()

    def language_changed(
        self,
        index=None,
    ):
        if not hasattr(
            self,
            "language_combo",
        ):
            return

        code = (
            self.language_combo.currentData()
            or I18N.DEFAULT_LANGUAGE
        )

        I18N.set_language(
            code
        )

        self.retranslate_ui()

        ok, message = save_full_config()

        if ok:
            self.feedback(
                "语言设置已保存。",
                3500,
            )
        else:
            self.msg_warning(
                "语言设置保存失败",
                "界面语言已经切换，但配置文件保存失败。",
                message,
            )

    def reload_language_files(
        self,
    ):
        I18N.reload_locales()

        code = (
            self.language_combo.currentData()
            if hasattr(
                self,
                "language_combo",
            )
            else I18N.get_language()
        )

        I18N.set_language(
            code
            or I18N.DEFAULT_LANGUAGE
        )

        self.retranslate_ui()

        self.feedback(
            "语言文件已重新加载。",
            4000,
        )

    def open_locale_folder(
        self,
    ):
        I18N.LOCALE_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        try:
            if sys.platform.startswith(
                "win"
            ):
                os.startfile(
                    str(
                        I18N.LOCALE_DIR
                    )
                )
            else:
                self.feedback(
                    str(
                        I18N.LOCALE_DIR
                    ),
                    5000,
                )

        except Exception as e:
            self.msg_error(
                "打开语言文件夹失败",
                "无法打开 language 文件夹。",
                str(e),
            )


    def build_root(self):
        c = QWidget()
        self.setCentralWidget(c)

        # StackAll 模式下“当前控件”会被放在最上层。
        # 旧版默认 currentIndex=0，刚好是背景层，因此背景会把整个 UI 盖住，
        # 看起来就只剩一个黑窗口。
        self.stack = QStackedLayout(c)
        self.stack.setStackingMode(
            QStackedLayout.StackingMode.StackAll
        )

        # 先加入背景层
        self.bg = BackgroundWidget()
        self.stack.addWidget(self.bg)

        # 再加入真正的 UI 内容层
        self.content = QWidget()
        self.content.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground,
            True
        )
        self.stack.addWidget(self.content)

        # 关键修复：明确把内容层设为当前层 / 最上层
        self.stack.setCurrentWidget(self.content)

        # 再做一次 QWidget 层级兜底。
        # 即使后续背景 invalidate/repaint，也不应该覆盖内容。
        self.bg.lower()
        self.content.raise_()

    def showEvent(self, event):
        super().showEvent(event)

        # Windows 首次显示窗口时最后再确保一次：
        # UI 在上、背景在下。
        if hasattr(self, "stack"):
            self.stack.setCurrentWidget(self.content)

        if hasattr(self, "bg"):
            self.bg.lower()

        if hasattr(self, "content"):
            self.content.raise_()

    def apply_theme(self, refresh_background=False):
        accent = appearance.get("accent", "#5B8CFF")
        r, g, b = rgb(accent)
        op = float(appearance.get("glass_opacity", .58))
        rad = int(appearance.get("glass_radius", 16))
        bop = float(appearance.get("glass_border_opacity", .28))
        panel = f"rgba(12,18,28,{int(op*255)})"
        soft = f"rgba(20,29,44,{int(min(.95,op+.08)*255)})"
        border = f"rgba(220,232,248,{int(bop*255)})"

        self.setStyleSheet(f'''
            QMainWindow{{background:#080C12}}
            QWidget{{color:#EAF0FA;font-family:"Microsoft YaHei UI","Segoe UI";font-size:13px}}
            QFrame#sidebar{{background:rgba(8,13,21,190);border:1px solid {border};border-radius:{rad}px}}
            QFrame#glassPanel,QFrame#glassCard,QGroupBox,QFrame#tierRow{{background:{panel};border:1px solid {border};border-radius:{rad}px}}
            QFrame#glassCard:hover{{background:rgba(18,27,42,{int(min(.95,op+.1)*255)});border-color:rgba({r},{g},{b},190)}}
            QFrame#tierRow{{background:rgba(255,255,255,10);border-radius:10px}}
            QGroupBox{{margin-top:12px;padding-top:12px;font-weight:600}}
            QGroupBox::title{{subcontrol-origin:margin;left:12px;padding:0 6px}}
            QLabel#title{{font-size:23px;font-weight:700}}
            QLabel#subtitle,QLabel#muted{{color:#94A3B8}}
            QLabel#cardTitle{{font-weight:700}}
            QLabel#statusValue{{color:rgb({r},{g},{b});font-weight:700;font-size:15px}}
            QPushButton{{background:{soft};border:1px solid {border};border-radius:9px;padding:8px 12px}}
            QPushButton:hover{{background:rgba({r},{g},{b},105);border-color:rgba({r},{g},{b},180)}}
            QPushButton#primary{{background:rgba({r},{g},{b},215);font-weight:700}}
            QPushButton#danger{{background:rgba(220,70,82,220);font-weight:700}}
            QPushButton#dangerGhost{{background:rgba(180,55,65,90);color:#FFCDD2}}
            QPushButton#navButton{{text-align:left;background:transparent;border:0;padding:10px 12px;color:#B9C6D9;font-size:14px}}
            QPushButton#navButton:checked{{background:rgba({r},{g},{b},95);color:white;border:1px solid rgba({r},{g},{b},160)}}
            QPushButton#sidebarToggle{{background:rgba(255,255,255,12);border:0}}
            QLineEdit,QTextEdit,QSpinBox,QDoubleSpinBox,QComboBox{{background:rgba(8,13,21,195);border:1px solid {border};border-radius:8px;padding:7px;min-height:22px}}
            QComboBox QAbstractItemView{{background:#111A28;color:#EAF0FA;selection-background-color:rgba({r},{g},{b},150)}}
            QProgressBar{{background:rgba(255,255,255,18);border:0;border-radius:3px}}
            QProgressBar::chunk{{background:rgb({r},{g},{b});border-radius:3px}}
            QTableWidget,QListWidget{{background:rgba(8,13,21,165);alternate-background-color:rgba(255,255,255,10);border:1px solid {border};border-radius:10px;gridline-color:rgba(255,255,255,18)}}
            QListWidget::item{{padding:9px;border-radius:7px}}
            QListWidget::item:selected{{background:rgba({r},{g},{b},105);color:white}}
            QHeaderView::section{{background:rgba(17,27,42,210);border:0;padding:8px;font-weight:700}}
            QScrollArea,QScrollArea>QWidget>QWidget{{background:transparent;border:0}}
            QMessageBox{{background:#111827}}
            QMessageBox QLabel{{color:#EAF0FA;background:transparent}}
            QSlider::groove:horizontal{{height:5px;background:rgba(255,255,255,30);border-radius:2px}}
            QSlider::handle:horizontal{{background:rgb({r},{g},{b});width:16px;margin:-6px 0;border-radius:8px}}
            QSplitter::handle{{background:rgba(255,255,255,18);width:2px;height:2px}}
        ''')
        if refresh_background:
            self.bg.invalidate()

        # Do not force an additional full-screen background repaint for
        # glass/accent changes. setStyleSheet() already invalidates affected
        # widgets by itself.
        for card in self.status_cards.values():
            card.set_value(
                card._last
            )

    def refresh_background_cache(self):
        """真正重建背景缓存。"""
        self.bg.invalidate()
        self.bg.update()

    def schedule_background_refresh(self):
        """
        避免拖动“模糊 / 亮度”滑块时每个数值变化都同步处理图片。
        """
        if hasattr(
            self,
            "_background_refresh_timer",
        ):
            self._background_refresh_timer.start()
        else:
            self.refresh_background_cache()

    def panel(self,title):
        p=QFrame();p.setObjectName("glassPanel");l=QVBoxLayout(p);l.setContentsMargins(14,12,14,12);l.setSpacing(8);t=QLabel(title);t.setStyleSheet("font-weight:700");l.addWidget(t);return p,l

    def build_ui(self):
        root = QHBoxLayout(self.content)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(196)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(10, 12, 10, 12)
        side.setSpacing(7)

        top = QHBoxLayout()
        self.side_title = QLabel("COYOTE")
        self.side_title.setStyleSheet("font-size:18px;font-weight:800")
        self.sidebar_toggle = QPushButton("≪")
        self.sidebar_toggle.setObjectName("sidebarToggle")
        self.sidebar_toggle.setFixedWidth(38)
        self.sidebar_toggle.clicked.connect(lambda: self.set_sidebar_collapsed(not self._sidebar_collapsed, manual=True))
        top.addWidget(self.side_title)
        top.addStretch(1)
        top.addWidget(self.sidebar_toggle)
        side.addLayout(top)

        sub = QLabel("PEAK × DG-LAB")
        sub.setObjectName("muted")
        self.side_subtitle = sub
        side.addWidget(sub)

        self.page_stack = QStackedWidget()
        pages = [
            ("⌂", "总览", "dashboard"),
            ("◎", "游戏遥测", "telemetry"),
            ("⚡", "电击规则", "rules"),
            ("≡", "事件日志", "logs"),
            ("⌁", "连接 / 配对", "pair"),
            ("◈", "设备控制", "device"),
            ("〽", "自定义波形", "wave"),
            ("⌘", "自定义编程", "code"),
            ("⚙", "设置", "settings"),
        ]

        self.dashboard = QWidget()
        self.telemetry = QWidget()
        self.rules_tab = QWidget()
        self.logs = QWidget()
        self.pair = QWidget()
        self.device_page = QWidget()
        self.wave = QWidget()
        self.code_page = QWidget()
        self.look = QWidget()
        widgets = [
            self.dashboard,
            self.telemetry,
            self.rules_tab,
            self.logs,
            self.pair,
            self.device_page,
            self.wave,
            self.code_page,
            self.look,
        ]

        for index, ((icon, label, key), widget) in enumerate(zip(pages, widgets)):
            btn = QPushButton(f"{icon}   {label}")
            btn.setObjectName("navButton")
            btn.setCheckable(True)
            btn.setProperty(
                "nav_icon",
                icon,
            )
            btn.setProperty(
                "nav_source_label",
                label,
            )
            btn.setProperty(
                "fullText",
                f"{icon}   {I18N.tr(label)}",
            )
            btn.setProperty(
                "shortText",
                icon,
            )
            btn.clicked.connect(lambda checked=False, i=index: self.switch_page(i))
            side.addWidget(btn)
            self.nav_buttons.append(btn)
            self.page_indices[key] = index
            self.page_stack.addWidget(widget)

        side.addStretch(1)
        self.side_peak = QLabel("PEAK  ·  等待")
        self.side_peak.setObjectName("muted")
        self.side_peak.setProperty(
            "i18n_dynamic",
            True,
        )

        self.side_dg = QLabel("DG APP · 未接入")
        self.side_dg.setObjectName("muted")
        self.side_dg.setProperty(
            "i18n_dynamic",
            True,
        )
        side.addWidget(self.side_peak)
        side.addWidget(self.side_dg)

        self.master = QCheckBox("允许电击输出")
        self.master.stateChanged.connect(self.toggle_master)
        side.addWidget(self.master)
        stop = QPushButton("立即停止")
        stop.setObjectName("danger")
        stop.clicked.connect(self.stop_output)
        side.addWidget(stop)

        root.addWidget(self.sidebar)

        right = QVBoxLayout()
        header = QFrame()
        header.setObjectName("glassPanel")
        hl = QHBoxLayout(header)
        title_box = QVBoxLayout()
        self.header_title = QLabel("总览")
        self.header_title.setObjectName("title")
        self.header_subtitle = QLabel("实时状态 · 输出摘要 · 最近事件")
        self.header_subtitle.setObjectName("subtitle")
        title_box.addWidget(self.header_title)
        title_box.addWidget(self.header_subtitle)
        hl.addLayout(title_box, 1)
        self.header_connection = QLabel("等待连接")
        self.header_connection.setObjectName("muted")
        self.header_connection.setProperty(
            "i18n_dynamic",
            True,
        )
        hl.addWidget(self.header_connection)
        right.addWidget(header)
        right.addWidget(self.page_stack, 1)
        root.addLayout(right, 1)

        self.build_dashboard()
        self.build_telemetry()
        self.build_rules()
        self.build_logs()
        self.build_pair()
        self.build_device_control()
        self.build_wave()
        self.build_custom_code()
        self.build_appearance()
        self.switch_page(0)

    def switch_page(self, index):
        if index < 0 or index >= self.page_stack.count():
            return
        self.page_stack.setCurrentIndex(index)
        for i, btn in enumerate(self.nav_buttons):
            btn.setChecked(i == index)
        titles = [
            ("总览", "实时状态 · 输出摘要 · 最近事件"),
            ("游戏遥测", "位置运动 · 手持物 · 使用/食用事件 · 全部状态 · 原始数据"),
            ("电击规则", "左侧直接选择规则，右侧立即编辑"),
            ("事件日志", "连接 · 游戏事件 · 输出记录"),
            ("连接 / 配对", "DG-LAB APP 扫码配对与设备状态"),
            ("设备控制", "A/B 通道 · 波形快捷选择 · 临时播放 · 一键停止"),
            ("自定义波形", "创建 · 校验 · JSON 导入导出"),
            ("自定义编程", "加载 Python 条件规则 · 示例 · 开发文档 · 软件介绍"),
            ("设置", "背景毛玻璃 · 显示选项 · PEAK/DG 端口"),
        ]
        self.header_title.setText(
            I18N.tr(
                titles[index][0]
            )
        )
        self.header_subtitle.setText(
            I18N.tr(
                titles[index][1]
            )
        )

    def set_sidebar_collapsed(self, collapsed, manual=False):
        self._sidebar_collapsed = bool(collapsed)
        self.sidebar.setFixedWidth(70 if collapsed else 196)
        self.side_title.setVisible(not collapsed)
        self.side_subtitle.setVisible(not collapsed)
        self.side_peak.setVisible(not collapsed)
        self.side_dg.setVisible(not collapsed)
        self.master.setText(
            I18N.tr(
                "输出"
                if collapsed
                else "允许电击输出"
            )
        )
        self.sidebar_toggle.setText("≫" if collapsed else "≪")
        for btn in self.nav_buttons:
            btn.setText(btn.property("shortText") if collapsed else btn.property("fullText"))
            btn.setStyleSheet("text-align:center" if collapsed else "")

    def resizeEvent(
        self,
        event,
    ):
        super().resizeEvent(
            event
        )

        # Sidebar width adjustment itself is cheap and should feel immediate.
        if (
            self.width() < 1080
            and not self._sidebar_collapsed
        ):
            self.set_sidebar_collapsed(
                True
            )

        elif (
            self.width() > 1220
            and self._sidebar_collapsed
        ):
            self.set_sidebar_collapsed(
                False
            )

        if hasattr(
            self,
            "_resize_refresh_timer",
        ):
            self._resize_refresh_timer.start()

    def _finish_resize_refresh(
        self,
    ):
        """
        Heavy resize work is performed once after resizing settles.
        """
        self.reflow_status_cards()

        if hasattr(
            self,
            "manual_wave_grid",
        ):
            self.refresh_manual_wave_buttons()

        if hasattr(
            self,
            "bg",
        ):
            self.bg.invalidate()

    def reflow_status_cards(self):
        if not hasattr(self, "status_grid"):
            return
        width = self.dashboard_scroll.viewport().width() if hasattr(self, "dashboard_scroll") else self.width()
        if width >= 1180:
            cols = 5
        elif width >= 940:
            cols = 4
        elif width >= 700:
            cols = 3
        else:
            cols = 2
        if cols == self._last_status_columns:
            return
        self._last_status_columns = cols
        for card in self.status_card_order:
            self.status_grid.removeWidget(card)
        for i, card in enumerate(self.status_card_order):
            self.status_grid.addWidget(card, i // cols, i % cols)
        for c in range(5):
            self.status_grid.setColumnStretch(c, 1 if c < cols else 0)

    def build_dashboard(self):
        outer = QVBoxLayout(self.dashboard)
        outer.setContentsMargins(0, 0, 0, 0)
        self.dashboard_scroll = QScrollArea()
        self.dashboard_scroll.setWidgetResizable(True)
        body = QWidget()
        l = QVBoxLayout(body)
        l.setContentsMargins(6, 6, 6, 6)
        l.setSpacing(10)

        top = QGridLayout()
        cp, cl = self.panel("连接状态")
        cg = QGridLayout()
        self.conn = {}
        rows = (
            ("peak", "PEAK"), ("peak_packet", "最近遥测"), ("scene", "场景"),
            ("dg", "DG APP"), ("device", "郊狼设备"), ("slot", "slotId"),
            ("ip", "本机 IP"), ("ports", "端口"), ("device_state", "设备状态"),
        )
        for r, (k, n) in enumerate(rows):
            cg.addWidget(QLabel(n), r, 0)
            v = QLabel("-")
            v.setAlignment(Qt.AlignmentFlag.AlignRight)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            cg.addWidget(v, r, 1)
            self.conn[k] = v
        cl.addLayout(cg)

        gp, gl = self.panel("游戏核心状态")
        gg = QGridLayout()
        self.core = {}
        items = (("hp","血量"),("injury","受伤"),("stamina","体力"),("stamina_max","体力上限"),("extra","额外体力"),("dead","死亡"),("passed","昏迷"),("climbing","攀爬"),("grounded","接地"),("crouching","蹲下"),("held","手持物"),("speed","速度"))
        for i, (k, n) in enumerate(items):
            r = i // 2; c = (i % 2) * 2
            gg.addWidget(QLabel(n), r, c)
            v = QLabel("-"); v.setAlignment(Qt.AlignmentFlag.AlignRight); v.setWordWrap(True)
            gg.addWidget(v, r, c + 1); self.core[k] = v
        gl.addLayout(gg)
        top.addWidget(cp, 0, 0)
        top.addWidget(gp, 0, 1)
        top.setColumnStretch(0, 1); top.setColumnStretch(1, 1)
        l.addLayout(top)

        sp, sl = self.panel("PEAK 状态")
        self.status_grid = QGridLayout()
        self.status_grid.setHorizontalSpacing(8); self.status_grid.setVerticalSpacing(8)
        for key, name in B.STATUS_ORDER:
            card = StatusCard(key, name)
            card.clicked.connect(self.open_rule)
            self.status_cards[key] = card
            self.status_card_order.append(card)
        sl.addLayout(self.status_grid)
        l.addWidget(sp)

        bottom = QGridLayout()
        rp, rl = self.panel("近期游戏 / 输出事件")
        self.recent = QTableWidget(0, 4)
        self.setup_table(self.recent, ["时间","类型","事件","内容"])
        self.recent.setMinimumHeight(220)
        rl.addWidget(self.recent)

        op, ol = self.panel("输出摘要")
        self.ocount = QLabel("0"); self.ocount.setStyleSheet("font-size:26px;font-weight:700")
        self.oevent = QLabel("-"); self.oevent.setWordWrap(True)
        self.oa = QLabel("-"); self.oa.setWordWrap(True)
        self.ob = QLabel("-"); self.ob.setWordWrap(True)
        for n, w in (("累计输出次数",self.ocount),("最近事件",self.oevent),("A 通道",self.oa),("B 通道",self.ob)):
            ol.addWidget(QLabel(n)); ol.addWidget(w)
        note = QLabel("强度为 DG-LAB 协议等级，不代表实际 mA。")
        note.setObjectName("muted"); note.setWordWrap(True); ol.addWidget(note); ol.addStretch(1)
        bottom.addWidget(rp, 0, 0)
        bottom.addWidget(op, 0, 1)
        bottom.setColumnStretch(0, 3); bottom.setColumnStretch(1, 1)
        l.addLayout(bottom)
        l.addStretch(1)

        self.dashboard_scroll.setWidget(body)
        outer.addWidget(self.dashboard_scroll)

    def setup_table(self,t,heads):
        t.setHorizontalHeaderLabels(heads)
        t.horizontalHeader().setSectionResizeMode(
            len(heads)-1,
            QHeaderView.ResizeMode.Stretch,
        )
        t.verticalHeader().setDefaultSectionSize(30)
        t.verticalHeader().setMinimumSectionSize(26)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

    def make_value_page(self, fields):
        page = QWidget()
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        content = QWidget(); form = QFormLayout(content)
        labels = {}
        for key, title in fields:
            value = QLabel("-")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(title, value)
            labels[key] = value
        scroll.setWidget(content)
        lay = QVBoxLayout(page); lay.setContentsMargins(0,0,0,0); lay.addWidget(scroll)
        return page, labels

    def build_telemetry(self):
        l = QHBoxLayout(self.telemetry)
        l.setContentsMargins(4,4,4,4)
        split = QSplitter(Qt.Orientation.Horizontal)

        left, ll = self.panel("游戏数据")
        self.telemetry_categories = QListWidget()
        for name in ("概览", "位置与运动", "物品与交互", "异常状态", "运行时扩展", "原始 JSON"):
            self.telemetry_categories.addItem(name)
        self.telemetry_categories.setMinimumWidth(180)
        self.telemetry_categories.setMaximumWidth(240)
        self.telemetry_categories.setWordWrap(True)
        self.telemetry_categories.setSpacing(3)
        ll.addWidget(self.telemetry_categories, 1)
        split.addWidget(left)

        self.telemetry_stack = QStackedWidget()
        overview_fields = [
            ("scene","当前场景"),("phase","阶段"),("hasCharacter","角色对象"),
            ("hp","血量"),("stamina","体力"),("held","手持物"),("speed","速度"),
            ("lastUse","最近使用"),("lastConsume","最近食用/消耗"),
        ]
        page, self.telemetry_overview = self.make_value_page(overview_fields)
        self.telemetry_stack.addWidget(page)

        pos_fields = [
            ("position","世界坐标 position"),("rotation","旋转 rotation"),("groundPos","地面坐标 groundPos"),
            ("lookDirection","视线方向"),("velocity","速度向量"),("speed","速度大小"),
            ("sinceClimb","sinceClimb"),("currentClimbHandle","当前攀爬点"),
            ("currentHeadHeight","当前头高"),("targetHeadHeight","目标头高"),("targetHipHeight","目标髋高"),
        ]
        page, self.telemetry_position = self.make_value_page(pos_fields)
        self.telemetry_stack.addWidget(page)

        items_page = QWidget(); items_l = QVBoxLayout(items_page)
        item_summary, self.telemetry_items = self.make_value_page([
            ("heldName","手持物名称"),("heldType","类型"),("heldId","实例 ID"),("selectedSlot","当前选择槽位"),
            ("pocketItems","口袋物品"),("backpackItems","背包物品"),
            ("lastItemEvent","最近物品事件"),("lastUsedItem","最近使用"),("lastConsumedItem","最近食用/消耗"),
        ])
        items_l.addWidget(item_summary, 1)
        self.held_detail_table = QTableWidget(0,2); self.setup_table(self.held_detail_table,["手持物字段","值"])
        self.held_detail_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        items_l.addWidget(self.held_detail_table, 1)
        self.telemetry_stack.addWidget(items_page)

        status_page = QWidget(); sl = QVBoxLayout(status_page)
        self.all_status_table = QTableWidget(0,4)
        self.setup_table(self.all_status_table,["索引","状态","原始名","当前值"])
        self.all_status_table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch)
        sl.addWidget(self.all_status_table)
        self.telemetry_stack.addWidget(status_page)

        extra_page = QWidget(); el = QVBoxLayout(extra_page)
        split_extra = QSplitter(Qt.Orientation.Vertical)
        self.char_extra_table = QTableWidget(0,2)
        self.setup_table(self.char_extra_table,["CharacterData 字段","值"])
        self.char_extra_table.horizontalHeader().setSectionResizeMode(
            0,QHeaderView.ResizeMode.ResizeToContents
        )
        self.item_extra_table = QTableWidget(0,2)
        self.setup_table(self.item_extra_table,["ItemSystem 字段","值"])
        self.item_extra_table.horizontalHeader().setSectionResizeMode(
            0,QHeaderView.ResizeMode.ResizeToContents
        )
        split_extra.addWidget(self.char_extra_table); split_extra.addWidget(self.item_extra_table)
        el.addWidget(split_extra)
        self.telemetry_stack.addWidget(extra_page)

        raw_page = QWidget(); rl = QVBoxLayout(raw_page)
        self.raw_json = QTextEdit(); self.raw_json.setReadOnly(True); self.raw_json.setFont(QFont("Consolas", 9))
        rl.addWidget(self.raw_json)
        self.telemetry_stack.addWidget(raw_page)

        split.addWidget(self.telemetry_stack)
        split.setChildrenCollapsible(False)
        left.setMinimumWidth(195)
        self.telemetry_stack.setMinimumWidth(360)
        split.setStretchFactor(0,0); split.setStretchFactor(1,1)
        split.setSizes([220,900])
        l.addWidget(split)
        self.telemetry_categories.currentRowChanged.connect(self.telemetry_stack.setCurrentIndex)
        self.telemetry_categories.setCurrentRow(0)

    @staticmethod
    def format_vector(value):
        if isinstance(value, dict):
            parts=[]
            for k in ("x","y","z","w"):
                if k in value:
                    try: parts.append(f"{k.upper()} {float(value[k]):.3f}")
                    except: parts.append(f"{k.upper()} {value[k]}")
            return "   ".join(parts) if parts else json.dumps(value,ensure_ascii=False)
        return str(value if value is not None else "-")

    @staticmethod
    def format_item_event(value):
        if not isinstance(value, dict) or not value.get("item"):
            return "-"
        inferred = "（推断）" if value.get("inferred") else ""
        detail = str(value.get("detail") or "")
        return f"{value.get('item','-')}{inferred}" + (f" · {detail}" if detail else "")

    def fill_kv_table(self, table, data):
        data = data if isinstance(data, dict) else {}
        items = sorted(data.items(), key=lambda kv: str(kv[0]).lower())
        table.setRowCount(len(items))
        for r,(k,v) in enumerate(items):
            table.setItem(r,0,QTableWidgetItem(str(k)))
            table.setItem(r,1,QTableWidgetItem(str(v)))

    def refresh_telemetry(self, p):
        if not p:
            return

        telemetry_index = self.page_indices.get("telemetry", -1)
        if telemetry_index >= 0 and self.page_stack.currentIndex() != telemetry_index:
            return

        category = self.telemetry_categories.currentRow()
        held = p.get("heldItem") if isinstance(p.get("heldItem"),dict) else {}
        inv = p.get("inventory") if isinstance(p.get("inventory"),dict) else {}

        if category == 0:
            overview = {
                "scene": str(p.get("scene") or "-"),
                "phase": str(p.get("phase") or "-"),
                "hasCharacter": self.yn(p.get("hasCharacter",True)),
                "hp": f"{float(p.get('hp',0)):.1f}%" if p.get("hasCharacter",True) else "-",
                "stamina": str(p.get("staminaCurrent","-")),
                "held": str(held.get("name") or "空手"),
                "speed": str(p.get("speed","-")),
                "lastUse": self.format_item_event(p.get("lastUsedItem")),
                "lastConsume": self.format_item_event(p.get("lastConsumedItem")),
            }
            for k,v in overview.items():
                self.telemetry_overview[k].setText(str(v))
            return

        if category == 1:
            for k in self.telemetry_position:
                value=p.get(k,"-")
                self.telemetry_position[k].setText(
                    self.format_vector(value)
                    if k in {"position","rotation","groundPos","lookDirection","velocity"}
                    else str(value)
                )
            return

        if category == 2:
            item_vals = {
                "heldName": (
                    held.get("name")
                    or I18N.tr("空手")
                ),
                "heldType": held.get("type") or "-",
                "heldId": held.get("instanceId") or "-",
                "selectedSlot": inv.get("selectedSlot") or "-",
                "pocketItems": "、".join(str(x) for x in inv.get("pocketItems",[]) if x) or "空",
                "backpackItems": "、".join(str(x) for x in inv.get("backpackItems",[]) if x) or "空",
                "lastItemEvent": self.format_item_event(p.get("lastItemEvent")),
                "lastUsedItem": self.format_item_event(p.get("lastUsedItem")),
                "lastConsumedItem": self.format_item_event(p.get("lastConsumedItem")),
            }
            for k,v in item_vals.items():
                self.telemetry_items[k].setText(str(v))
            self.fill_kv_table(self.held_detail_table, held.get("details"))
            return

        if category == 3:
            statuses=p.get("statuses") if isinstance(p.get("statuses"),list) else []
            names=p.get("statusNames") if isinstance(p.get("statusNames"),list) else []
            count=max(len(statuses),len(names))
            self.all_status_table.setRowCount(count)
            for i in range(count):
                raw=str(names[i]) if i<len(names) else f"Status{i}"
                zh=I18N.tr(
                    B.STATUS_TRANSLATIONS.get(
                        raw,
                        raw,
                    )
                )
                val=statuses[i] if i<len(statuses) else 0
                try: shown=f"{float(val)*100:.1f}%"
                except Exception: shown=str(val)
                for c,x in enumerate((i,zh,raw,shown)):
                    self.all_status_table.setItem(i,c,QTableWidgetItem(str(x)))
            return

        if category == 4:
            self.fill_kv_table(self.char_extra_table,p.get("characterDataExtra"))
            self.fill_kv_table(self.item_extra_table,inv.get("extra"))
            return

        if category == 5:
            self.raw_json.setPlainText(json.dumps(p,ensure_ascii=False,indent=2))
    def build_rules(self):
        l = QVBoxLayout(self.rules_tab)
        l.setContentsMargins(4,4,4,4)

        bar = QHBoxLayout()
        note = QLabel("左侧直接点击规则；可单独勾选，也可以按组一键启停。")
        note.setObjectName("muted")
        note.setWordWrap(True)
        bar.addWidget(note,1)

        self.apply_rules_button = QPushButton("应用参数")
        self.apply_rules_button.setObjectName("primary")
        self.apply_rules_button.clicked.connect(self.apply_rules)
        self.batch_apply_rules_button = QPushButton("批量套用到已勾选")
        self.batch_apply_rules_button.clicked.connect(self.batch_apply_checked_rules)
        save_button=QPushButton("保存"); save_button.clicked.connect(self.save_rules)
        import_button=QPushButton("导入 JSON"); import_button.clicked.connect(self.import_settings_json)
        export_button=QPushButton("导出 JSON"); export_button.clicked.connect(self.export_settings_json)
        reset_button=QPushButton("恢复默认"); reset_button.clicked.connect(self.reset_rules)
        for w in (self.apply_rules_button,self.batch_apply_rules_button,save_button,import_button,export_button,reset_button):
            bar.addWidget(w)
        l.addLayout(bar)

        groups = QHBoxLayout()
        groups.setSpacing(6)
        all_on=QPushButton("全部开启")
        all_on.clicked.connect(lambda:self.set_all_rules_enabled(True))
        all_off=QPushButton("全部关闭")
        all_off.clicked.connect(lambda:self.set_all_rules_enabled(False))
        groups.addWidget(all_on); groups.addWidget(all_off)

        self.group_checkboxes={}
        for group_id,title,_ in B.RULE_GROUPS:
            cb=QCheckBox(title)
            cb.setTristate(True)
            cb.clicked.connect(
                lambda checked=False,gid=group_id:
                self.set_rule_group_enabled(gid,checked)
            )
            self.group_checkboxes[group_id]=cb
            groups.addWidget(cb)
        groups.addStretch(1)
        l.addLayout(groups)

        split=QSplitter(Qt.Orientation.Horizontal)
        left,ll=self.panel("规则")
        self.rule_search=QLineEdit()
        self.rule_search.setPlaceholderText("搜索：受伤 / 中毒 / 跳跃 / 背包")
        self.rule_search.textChanged.connect(self.filter_rules)
        ll.addWidget(self.rule_search)
        self.rule_list=QListWidget()
        self.rule_list.setWordWrap(True)
        self.rule_list.setSpacing(2)
        ll.addWidget(self.rule_list,1)
        split.addWidget(left)

        self.rule_stack=QStackedWidget()
        for index,(key,display,_,trigger) in enumerate(B.RULE_META):
            item=QListWidgetItem(f"{display}\n{trigger}")
            item.setData(Qt.ItemDataRole.UserRole,key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.rule_list.addItem(item)
            self.rule_list_items[key]=item

            editor=RuleEditor(key,display,trigger)
            self.rule_editors[key]=editor
            editor.enabled.toggled.connect(
                lambda checked,k=key:
                self.rule_editor_enabled_changed(k,checked)
            )
            scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(editor)
            self.rule_stack.addWidget(scroll)

        split.addWidget(self.rule_stack)
        split.setChildrenCollapsible(False)
        left.setMinimumWidth(220)
        self.rule_stack.setMinimumWidth(360)
        split.setStretchFactor(0,0); split.setStretchFactor(1,1)
        split.setSizes([285,900])
        l.addWidget(split,1)

        self.rule_list.currentRowChanged.connect(self.rule_stack.setCurrentIndex)
        self.rule_list.itemChanged.connect(self.rule_list_item_changed)
        self.rule_list.setCurrentRow(0)

    def rule_list_item_changed(self,item):
        key=item.data(Qt.ItemDataRole.UserRole)
        if key not in self.rule_editors:
            return
        enabled=(item.checkState()==Qt.CheckState.Checked)
        editor=self.rule_editors[key]
        if editor.enabled.isChecked()!=enabled:
            editor.enabled.blockSignals(True)
            editor.enabled.setChecked(enabled)
            editor.enabled.blockSignals(False)
        self.update_rule_group_states()

    def rule_editor_enabled_changed(self,key,enabled):
        item=self.rule_list_items.get(key)
        if item is not None:
            desired=Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked
            if item.checkState()!=desired:
                self.rule_list.blockSignals(True)
                item.setCheckState(desired)
                self.rule_list.blockSignals(False)
        self.update_rule_group_states()

    def set_rule_group_enabled(self,group_id,enabled):
        keys=next((keys for gid,_,keys in B.RULE_GROUPS if gid==group_id),[])
        for key in keys:
            editor=self.rule_editors.get(key)
            if editor is not None:
                editor.enabled.setChecked(bool(enabled))
        self.update_rule_group_states()

    def set_all_rules_enabled(self,enabled):
        for editor in self.rule_editors.values():
            editor.enabled.setChecked(bool(enabled))
        self.update_rule_group_states()
        self.feedback("全部规则已开启。" if enabled else "全部规则已关闭。",2500)

    def update_rule_group_states(self):
        if not hasattr(self,"group_checkboxes"):
            return
        for group_id,_,keys in B.RULE_GROUPS:
            states=[self.rule_editors[key].enabled.isChecked() for key in keys if key in self.rule_editors]
            cb=self.group_checkboxes.get(group_id)
            if cb is None:
                continue
            cb.blockSignals(True)
            if states and all(states):
                cb.setCheckState(Qt.CheckState.Checked)
            elif states and any(states):
                cb.setCheckState(Qt.CheckState.PartiallyChecked)
            else:
                cb.setCheckState(Qt.CheckState.Unchecked)
            cb.blockSignals(False)

    def filter_rules(self, text):
        query=str(text or "").strip().lower()
        for i in range(self.rule_list.count()):
            item=self.rule_list.item(i)
            item.setHidden(query not in item.text().lower())
        # 自动选中第一个可见项
        current=self.rule_list.currentItem()
        if current is None or current.isHidden():
            for i in range(self.rule_list.count()):
                if not self.rule_list.item(i).isHidden():
                    self.rule_list.setCurrentRow(i); break

    def load_rules(self):
        with B.rule_lock:
            snap=json.loads(json.dumps(B.rules,ensure_ascii=False))
        for key,editor in self.rule_editors.items():
            editor.load_rule(snap[key])
            item=self.rule_list_items.get(key)
            if item is not None:
                self.rule_list.blockSignals(True)
                item.setCheckState(
                    Qt.CheckState.Checked if editor.enabled.isChecked() else Qt.CheckState.Unchecked
                )
                self.rule_list.blockSignals(False)
        self.update_rule_group_states()

    def batch_apply_checked_rules(self):
        current_item = self.rule_list.currentItem()
        if current_item is None:
            self.feedback("请先打开一个规则作为参数来源。", 3000)
            return

        source_key = current_item.data(Qt.ItemDataRole.UserRole)
        source_editor = self.rule_editors.get(source_key)
        if source_editor is None:
            return

        checked_keys = []
        for i in range(self.rule_list.count()):
            item = self.rule_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                checked_keys.append(item.data(Qt.ItemDataRole.UserRole))

        if not checked_keys:
            self.feedback("没有已勾选的规则。先勾选需要批量设置的规则。", 3500)
            return

        source = source_editor.data()
        common_fields = (
            "intensity_a", "intensity_b", "max_intensity_a", "max_intensity_b",
            "play_time_a", "play_time_b", "waveform_a", "waveform_b",
            "cooldown", "trigger_mode",
            "random_intensity", "random_min_a", "random_max_a",
            "random_min_b", "random_max_b",
            "spike_enabled", "spike_delta", "spike_add_a", "spike_add_b", "spike_tiers",
        )

        for key in checked_keys:
            editor = self.rule_editors.get(key)
            if editor is None:
                continue
            merged = editor.data()
            for field in common_fields:
                merged[field] = source.get(field, merged.get(field))
            # 动态百分比档位 / 瞬时变化档位只在来源和目标都支持时复制。
            if B.rule_supports_percentage_tiers(source_key) and B.rule_supports_percentage_tiers(key):
                merged["thresholds"] = json.loads(json.dumps(source.get("thresholds", []), ensure_ascii=False))
                merged["spike_tiers"] = json.loads(json.dumps(source.get("spike_tiers", []), ensure_ascii=False))
            merged["enabled"] = True
            editor.load_rule(merged)

        self.apply_rules(show=False)
        self.update_rule_group_states()
        self.feedback(f"已把“{source_editor.display}”的电击参数批量套用到 {len(checked_keys)} 条已勾选规则。", 5000)

    def apply_rules(
        self,
        show=True,
    ):
        new = {
            k: e.data()
            for k, e
            in self.rule_editors.items()
        }

        with B.rule_lock:
            B.rules.clear()
            B.rules.update(
                new
            )

        B.add_log(
            "系统",
            "规则参数已应用",
            "Qt GUI → 运行时",
        )

        # 按钮自身给出明显反馈，不再只能依赖弹窗或日志。
        if hasattr(
            self,
            "apply_rules_button",
        ):
            self.apply_rules_button.setText(
                "✓ 已应用"
            )

            self.apply_rules_button.setEnabled(
                False
            )

            QTimer.singleShot(
                1200,
                self.restore_apply_button,
            )

        self.feedback(
            f"✓ {len(B.RULE_META)} 条电击规则已应用到运行时。",
            4500,
        )

        # “应用”属于高频操作，默认不再强制弹阻塞式窗口。
        # 保存 / 导入 / 导出等操作仍然会给详细弹窗。
        if show:
            B.add_log(
                "系统",
                "规则应用反馈",
                "按钮已显示 ✓ 已应用",
            )

    def restore_apply_button(self):
        if not hasattr(
            self,
            "apply_rules_button",
        ):
            return

        self.apply_rules_button.setText(
            "应用参数"
        )

        self.apply_rules_button.setEnabled(
            True
        )

    def build_settings_export_payload(self):
        """
        导出“参数设置”，不包含背景图片二进制，也不会导出总输出开关状态。

        这样把 JSON 导入另一台电脑时，不会因为导入配置而自动开启输出。
        """
        current_rules = {
            key: editor.data()
            for key, editor
            in self.rule_editors.items()
        }

        export_appearance = {
            key: value
            for key, value
            in appearance.items()
            if key not in {
                "background_image",
                "background_enabled",
            }
        }

        return {
            "format": "coyote-settings-v2",
            "version": 2,
            "rules": current_rules,
            "appearance": export_appearance,
            "network": {
                "peak_port": int(self.peak_port_spin.value()) if hasattr(self, "peak_port_spin") else int(B.network_settings.get("peak_port", B.DEFAULT_PEAK_PORT)),
                "dg_port": int(self.dg_port_spin.value()) if hasattr(self, "dg_port_spin") else int(B.network_settings.get("dg_port", B.DEFAULT_DG_PORT)),
            },
            "notes": {
                "background_image": (
                    "背景图片文件不包含在参数 JSON 中"
                ),
                "master_output": (
                    "总输出开关不会导入或导出"
                ),
            },
        }

    def export_settings_json(self):
        default_name = (
            "coyote_settings.json"
        )

        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出参数 JSON",
            str(
                B.ROOT
                / default_name
            ),
            "JSON (*.json)",
        )

        if not path:
            self.feedback(
                "已取消导出参数。",
                2500,
            )
            return

        if not path.lower().endswith(
            ".json"
        ):
            path += ".json"

        try:
            payload = (
                self.build_settings_export_payload()
            )

            Path(path).write_text(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            self.msg_info(
                "参数导出成功",
                "当前规则和界面参数已经导出为 JSON。",
                (
                    f"文件：{path}\n"
                    "不包含背景图片文件，也不会保存“允许电击输出”开关状态。"
                ),
            )

        except Exception as e:
            self.msg_error(
                "参数导出失败",
                "无法写入参数 JSON。",
                f"{type(e).__name__}: {e}",
            )

    def validate_imported_rules(
        self,
        incoming,
    ):
        if not isinstance(
            incoming,
            dict,
        ):
            raise ValueError(
                "rules 必须是 JSON 对象。"
            )

        validated = {}

        for key, _, _, _ in B.RULE_META:
            source = incoming.get(
                key
            )

            if not isinstance(
                source,
                dict,
            ):
                # 缺失的规则沿用当前值
                with B.rule_lock:
                    source = json.loads(
                        json.dumps(
                            B.rules[
                                key
                            ],
                            ensure_ascii=False,
                        )
                    )

            wa = str(
                source.get(
                    "waveform_a",
                    "脉冲",
                )
            )

            wb = str(
                source.get(
                    "waveform_b",
                    "脉冲",
                )
            )

            if wa not in B.COYOTE_WAVEFORMS:
                wa = "脉冲"

            if wb not in B.COYOTE_WAVEFORMS:
                wb = "脉冲"

            validated[
                key
            ] = {
                "enabled": bool(
                    source.get(
                        "enabled",
                        False,
                    )
                ),
                "intensity_a": B.clamp_int(
                    source.get(
                        "intensity_a",
                        5,
                    )
                ),
                "intensity_b": B.clamp_int(
                    source.get(
                        "intensity_b",
                        5,
                    )
                ),
                "max_intensity_a": B.clamp_int(
                    source.get(
                        "max_intensity_a",
                        10,
                    )
                ),
                "max_intensity_b": B.clamp_int(
                    source.get(
                        "max_intensity_b",
                        10,
                    )
                ),
                "play_time_a": B.clamp_duration(
                    source.get(
                        "play_time_a",
                        5000,
                    )
                ),
                "play_time_b": B.clamp_duration(
                    source.get(
                        "play_time_b",
                        5000,
                    )
                ),
                "waveform_a": wa,
                "waveform_b": wb,
                "cooldown": B.clamp_cooldown(
                    source.get(
                        "cooldown",
                        2.0,
                    )
                ),
                "trigger_mode": (
                    "repeat"
                    if str(
                        source.get(
                            "trigger_mode",
                            "single",
                        )
                        or "single"
                    ).lower()
                    == "repeat"
                    else "single"
                ),
                "trigger_delta": max(
                    0.1,
                    min(
                        100.0,
                        float(
                            source.get(
                                "trigger_delta",
                                1.0,
                            )
                        ),
                    ),
                ),
                "item_filter": str(
                    source.get(
                        "item_filter",
                        "",
                    )
                    or ""
                )[:500],
                "speed_threshold": max(
                    0.0,
                    min(
                        1000.0,
                        float(
                            source.get(
                                "speed_threshold",
                                (
                                    1.0
                                    if key == "speedBelow"
                                    else 5.0
                                ),
                            )
                        ),
                    ),
                ),
                "random_intensity": bool(source.get("random_intensity", False)),
                "random_min_a": B.normalize_intensity_range(source.get("random_min_a", 1), source.get("random_max_a", 5), 1, 5)[0],
                "random_max_a": B.normalize_intensity_range(source.get("random_min_a", 1), source.get("random_max_a", 5), 1, 5)[1],
                "random_min_b": B.normalize_intensity_range(source.get("random_min_b", 1), source.get("random_max_b", 5), 1, 5)[0],
                "random_max_b": B.normalize_intensity_range(source.get("random_min_b", 1), source.get("random_max_b", 5), 1, 5)[1],
                "spike_enabled": bool(source.get("spike_enabled", False)),
                "spike_delta": max(0.1, min(100.0, float(source.get("spike_delta", 50.0)))),
                "spike_add_a": B.clamp_int(source.get("spike_add_a", 5)),
                "spike_add_b": B.clamp_int(source.get("spike_add_b", 5)),
                "spike_tiers": (
                    B.normalize_spike_tiers(source.get("spike_tiers", []))
                    if B.rule_supports_percentage_tiers(key)
                    else []
                ),
                "thresholds": (
                    B.normalize_thresholds(
                        source.get(
                            "thresholds",
                            [],
                        )
                    )
                    if B.rule_supports_percentage_tiers(
                        key
                    )
                    else []
                ),
            }

        return validated

    def import_settings_json(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入参数 JSON",
            str(B.ROOT),
            "JSON (*.json)",
        )

        if not path:
            self.feedback(
                "已取消导入参数。",
                2500,
            )
            return

        try:
            data = json.loads(
                Path(path).read_text(
                    encoding="utf-8"
                )
            )

            if not isinstance(
                data,
                dict,
            ):
                raise ValueError(
                    "JSON 顶层必须是对象。"
                )

            fmt = data.get(
                "format"
            )

            # 兼容本程序导出的格式，也兼容直接保存的 coyote_gui_config.json
            if fmt not in (
                None,
                "coyote-settings-v1",
                "coyote-settings-v2",
            ):
                raise ValueError(
                    f"不支持的参数格式：{fmt}"
                )

            imported_rules = (
                self.validate_imported_rules(
                    data.get(
                        "rules",
                        {},
                    )
                )
            )

            incoming_appearance = (
                data.get(
                    "appearance",
                    {}
                )
            )

            if (
                incoming_appearance
                is not None
                and not isinstance(
                    incoming_appearance,
                    dict,
                )
            ):
                raise ValueError(
                    "appearance 必须是 JSON 对象。"
                )

            incoming_network=data.get("network",{}) or {}
            if not isinstance(incoming_network,dict):
                raise ValueError("network 必须是 JSON 对象。")

            if not self.ask_confirm(
                "导入参数",
                "确定导入这份参数文件吗？",
                (
                    f"文件：{path}\n"
                    "将覆盖当前规则编辑器中的参数和可兼容的外观参数。\n"
                    "背景图片文件不会替换；总输出开关不会自动开启。"
                ),
                confirm_text="导入",
                cancel_text="取消",
            ):
                return

            with B.rule_lock:
                B.rules.clear()
                B.rules.update(
                    imported_rules
                )

            # 外观仅导入参数，不覆盖实际背景图片路径。
            allowed_appearance = {
                key
                for key
                in APPEARANCE_DEFAULTS
                if key not in {
                    "background_image",
                    "background_enabled",
                }
            }

            for key, value in (
                incoming_appearance
                or {}
            ).items():
                if key in allowed_appearance:
                    appearance[
                        key
                    ] = value

            if incoming_network:
                imported_peak=B.validate_port(incoming_network.get("peak_port",B.network_settings.get("peak_port",B.DEFAULT_PEAK_PORT)),B.DEFAULT_PEAK_PORT)
                imported_dg=B.validate_port(incoming_network.get("dg_port",B.network_settings.get("dg_port",B.DEFAULT_DG_PORT)),B.DEFAULT_DG_PORT)
                if imported_peak != imported_dg:
                    B.network_settings["peak_port"]=imported_peak
                    B.network_settings["dg_port"]=imported_dg
                    if hasattr(self,"peak_port_spin"):
                        self.peak_port_spin.setValue(imported_peak)
                        self.dg_port_spin.setValue(imported_dg)

            # 导入永远不会开启总输出
            B.master_output_enabled = False
            self.master.setChecked(
                False
            )

            self.load_rules()
            self.load_appearance_controls()

            self.apply_theme()
            self.schedule_background_refresh()

            ok_save, message = (
                save_full_config()
            )

            if ok_save:
                self.msg_info(
                    "参数导入成功",
                    "规则和可兼容的外观参数已经导入并保存。",
                    (
                        "总输出仍保持关闭。\n"
                        "背景图片文件没有被参数 JSON 替换。"
                    ),
                )
            else:
                self.msg_warning(
                    "参数已导入，但保存失败",
                    "参数已经应用到当前运行时，但配置文件写入失败。",
                    message,
                )

        except Exception as e:
            self.msg_error(
                "参数导入失败",
                "无法读取或应用这个 JSON 文件。",
                f"{type(e).__name__}: {e}",
            )

    def save_rules(self):
        self.apply_rules(
            False
        )

        ok, msg = save_full_config()

        if ok:
            self.msg_info(
                "保存成功",
                "规则配置已经保存。",
                f"配置文件：{B.CONFIG_FILE}",
            )
        else:
            self.msg_error(
                "保存失败",
                "无法保存规则配置。",
                msg,
            )

    def reset_rules(self):
        if not self.ask_confirm(
            "恢复默认规则",
            f"确定把 {len(B.RULE_META)} 条电击规则恢复为默认值吗？",
            "当前界面中尚未保存的强度、时间、波形和动态档位设置都会被覆盖。",
            confirm_text="恢复默认",
            cancel_text="取消",
        ):
            return

        with B.rule_lock:
            for k, _, _, _ in B.RULE_META:
                B.rules[k] = (
                    B.default_rule()
                )

        self.load_rules()

        B.add_log(
            "系统",
            "规则恢复默认",
            f"{len(B.RULE_META)} 条规则已恢复默认值",
        )

        self.msg_info(
            "恢复完成",
            f"{len(B.RULE_META)} 条规则已经恢复为默认值。",
            "如需下次启动继续使用这些默认值，请点击“保存到文件”。",
        )

    def open_rule(self, key):
        self.switch_page(self.page_indices.get("rules", 2))
        for i in range(self.rule_list.count()):
            item=self.rule_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole)==key:
                self.rule_search.clear()
                self.rule_list.setCurrentRow(i)
                self.rule_list.scrollToItem(item)
                break

    def build_logs(self):
        l = QVBoxLayout(
            self.logs
        )

        p, pl = self.panel(
            "完整事件日志"
        )

        top = QHBoxLayout()

        n = QLabel(
            "记录连接、游戏事件、命中的强度档位、波形和实际发送参数。"
        )
        n.setObjectName(
            "muted"
        )
        n.setWordWrap(
            True
        )
        top.addWidget(
            n,
            1,
        )

        self.log_export_language = QComboBox()
        self.log_export_language.setProperty(
            "i18n_skip",
            True,
        )

        for code in I18N.available_languages():
            self.log_export_language.addItem(
                I18N.language_name(
                    code
                ),
                code,
            )

        current_code = I18N.get_language()
        current_index = (
            self.log_export_language.findData(
                current_code
            )
        )

        if current_index >= 0:
            self.log_export_language.setCurrentIndex(
                current_index
            )

        top.addWidget(
            QLabel(
                "导出语言"
            )
        )
        top.addWidget(
            self.log_export_language
        )

        export_button = QPushButton(
            "导出日志"
        )
        export_button.setObjectName(
            "primary"
        )
        export_button.clicked.connect(
            self.export_logs
        )
        top.addWidget(
            export_button
        )

        c = QPushButton(
            "清空全部日志"
        )
        c.clicked.connect(
            self.clear_logs
        )
        top.addWidget(
            c
        )

        pl.addLayout(
            top
        )

        search_row = QHBoxLayout()
        self.log_search = QLineEdit()
        self.log_search.setPlaceholderText("查找日志：时间 / 类型 / 事件 / 详细内容")
        self.log_search.textChanged.connect(lambda _=None: self.refresh_logs(force=True))
        self.log_search_count = QLabel("全部")
        self.log_search_count.setObjectName("muted")
        search_row.addWidget(self.log_search, 1)
        search_row.addWidget(self.log_search_count)
        pl.addLayout(search_row)

        self.logtable = QTableWidget(
            0,
            4,
        )

        self.setup_table(
            self.logtable,
            [
                "时间",
                "类型",
                "事件",
                "详细内容",
            ],
        )

        pl.addWidget(
            self.logtable
        )

        l.addWidget(
            p
        )

    def export_logs(self):
        language = (
            self.log_export_language.currentData()
            or I18N.get_language()
        )

        default_name = (
            "coyote-log-"
            + time.strftime(
                "%Y%m%d-%H%M%S"
            )
            + ".csv"
        )

        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            I18N.tr(
                "导出日志"
            ),
            str(
                B.LOG_DIR
                / default_name
            ),
            (
                "CSV (*.csv);;"
                "JSON (*.json);;"
                "Text (*.txt)"
            ),
        )

        if not path:
            self.feedback(
                "已取消日志导出。",
                2500,
            )
            return

        file_path = Path(
            path
        )

        if not file_path.suffix:
            if "JSON" in selected_filter:
                file_path = file_path.with_suffix(
                    ".json"
                )
            elif "Text" in selected_filter:
                file_path = file_path.with_suffix(
                    ".txt"
                )
            else:
                file_path = file_path.with_suffix(
                    ".csv"
                )

        raw_logs = B.read_all_event_logs()

        localized = [
            I18N.localize_log_record(
                item,
                language,
            )
            for item in raw_logs
        ]

        headers = {
            "time": I18N.tr(
                "时间",
                language,
            ),
            "category": I18N.tr(
                "类型",
                language,
            ),
            "event": I18N.tr(
                "事件",
                language,
            ),
            "detail": I18N.tr(
                "详细内容",
                language,
            ),
        }

        try:
            file_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            suffix = (
                file_path.suffix.lower()
            )

            if suffix == ".json":
                payload = []

                for item in localized:
                    payload.append({
                        headers["time"]:
                            item["time"],
                        headers["category"]:
                            item["category"],
                        headers["event"]:
                            item["event"],
                        headers["detail"]:
                            item["detail"],
                    })

                file_path.write_text(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

            elif suffix == ".txt":
                with file_path.open(
                    "w",
                    encoding="utf-8",
                ) as handle:
                    for item in localized:
                        handle.write(
                            (
                                f"[{item['time']}] "
                                f"{item['category']} | "
                                f"{item['event']} | "
                                f"{item['detail']}\n"
                            )
                        )

            else:
                # utf-8-sig makes Chinese/Japanese CSV display
                # correctly when opened directly with Excel.
                with file_path.open(
                    "w",
                    encoding="utf-8-sig",
                    newline="",
                ) as handle:
                    writer = csv.writer(
                        handle
                    )

                    writer.writerow([
                        headers["time"],
                        headers["category"],
                        headers["event"],
                        headers["detail"],
                    ])

                    for item in localized:
                        writer.writerow([
                            item["time"],
                            item["category"],
                            item["event"],
                            item["detail"],
                        ])

            self.msg_info(
                "日志导出成功",
                (
                    f"已导出 {len(localized)} 条日志。"
                ),
                (
                    f"文件：{file_path}\n"
                    f"语言："
                    f"{I18N.language_name(language)}"
                ),
            )

        except Exception as e:
            self.msg_error(
                "日志导出失败",
                "无法写入日志文件。",
                str(e),
            )

    def clear_logs(self):
        ok, message = B.clear_event_logs(clear_disk=True)
        self.logtable.setRowCount(0)
        self.recent.setRowCount(0)
        self.last_log_count = -1
        self.last_log_revision = -1
        if hasattr(self, "log_search_count"):
            self.log_search_count.setText("0 条")

        if ok:
            self.feedback("日志已完全清空：界面缓存和磁盘事件日志都已删除。", 4500)
        else:
            self.feedback(f"内存日志已清空，但磁盘日志清理失败：{message}", 5500)

    def build_pair(self):
        l=QHBoxLayout(self.pair);a,al=self.panel("DG-LAB 配对");self.qr=QLabel("等待控制方 ID...");self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter);self.qr.setMinimumSize(360,360);al.addWidget(self.qr,1);self.url=QLineEdit();self.url.setReadOnly(True);al.addWidget(self.url);cp=QPushButton("复制配对地址");cp.clicked.connect(self.copy_pair_url);al.addWidget(cp,alignment=Qt.AlignmentFlag.AlignRight);b,bl=self.panel("连接详细信息");f=QFormLayout();self.detail={}
        for k,n in (("server","DG Server"),("controller","控制方 ID"),("app","APP ID"),("device","郊狼设备"),("slot","slotId"),("error","错误")):v=QLabel("-");v.setWordWrap(True);self.detail[k]=v;f.addRow(n,v)
        bl.addLayout(f);bl.addStretch();l.addWidget(a,1);l.addWidget(b,1)

    def copy_pair_url(self):
        url = self.url.text().strip()

        if not url:
            self.msg_warning(
                "无法复制",
                "当前还没有可用的配对地址。",
                "请等待 DG-LAB 控制端建立连接并生成 controller ID。",
            )
            return

        QApplication.clipboard().setText(
            url
        )

        self.feedback(
            "配对地址已复制到剪贴板。",
            3000,
        )

    def update_qr(self,url):
        if url==self.qr_url:return
        self.qr_url=url
        if not url:self.qr.setText("等待控制方 ID...");self.qr.setPixmap(QPixmap());return
        try:
            im=qrcode.make(url);bio=BytesIO();im.save(bio,"PNG");pm=QPixmap();pm.loadFromData(bio.getvalue());self.qr.setText("");self.qr.setPixmap(pm.scaled(380,380,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        except Exception as e:self.qr.setText(f"二维码生成失败：{e}")

    # --------------------------------------------------------
    # 设备控制
    # --------------------------------------------------------

    def build_device_control(self):
        outer = QVBoxLayout(self.device_page)
        outer.setContentsMargins(6,6,6,6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setSpacing(12)

        head, hl = self.panel("郊狼设备控制")
        self.manual_device_label = QLabel("等待设备接入")
        self.manual_device_label.setStyleSheet("font-size:18px;font-weight:700")
        self.manual_slot_label = QLabel("slotId: -")
        self.manual_slot_label.setObjectName("muted")
        note = QLabel(
            "强度数字为 DG-LAB 协议等级，不代表实际 mA。"
            "手动播放同样要求先开启左侧“允许电击输出”。"
            "持续时间可填 -1：Coyote 会启动手动持续会话并按有限片段续播，"
            "直到点击“立即停止”、关闭总输出、设备断开或程序退出。"
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        hl.addWidget(self.manual_device_label)
        hl.addWidget(self.manual_slot_label)
        hl.addWidget(note)
        layout.addWidget(head)

        channels = QHBoxLayout()
        self.manual_controls = {}

        for channel, label in ((0,"A 通道"),(1,"B 通道")):
            box = QGroupBox(label)
            g = QGridLayout(box)

            level = QSpinBox()
            level.setRange(0,B.GUI_INTENSITY_MAX)
            level.setValue(5)

            duration = DurationSpinBox()
            duration.setValue(1500)

            wave = QComboBox()
            wave.addItems(B.waveform_names())
            wave.setCurrentText("脉冲")

            minus = QPushButton("－")
            plus = QPushButton("＋")
            minus.setMinimumHeight(44)
            plus.setMinimumHeight(44)
            minus.clicked.connect(
                lambda checked=False,s=level:
                s.setValue(s.value()-1)
            )
            plus.clicked.connect(
                lambda checked=False,s=level:
                s.setValue(s.value()+1)
            )

            play = QPushButton(f"▶ 播放 {label}")
            play.setObjectName("primary")
            play.clicked.connect(
                lambda checked=False,ch=channel:
                self.manual_send_channel(ch)
            )

            g.addWidget(QLabel("协议强度等级"),0,0)
            g.addWidget(level,0,1,1,2)
            g.addWidget(minus,1,1)
            g.addWidget(plus,1,2)
            g.addWidget(QLabel("持续时间"),2,0)
            g.addWidget(duration,2,1,1,2)
            g.addWidget(QLabel("波形"),3,0)
            g.addWidget(wave,3,1,1,2)
            g.addWidget(play,4,0,1,3)

            self.manual_controls[channel] = {
                "level": level,
                "duration": duration,
                "wave": wave,
            }
            channels.addWidget(box,1)

        layout.addLayout(channels)

        action, al = self.panel("双通道 / 快捷控制")
        row = QHBoxLayout()
        self.manual_link_channels = QCheckBox("联动 A/B 参数")
        play_both = QPushButton("▶ 播放 A + B")
        play_both.setObjectName("primary")
        play_both.clicked.connect(self.manual_send_both)
        stop = QPushButton("■ 立即停止全部输出")
        stop.setObjectName("danger")
        stop.clicked.connect(self.stop_output)
        row.addWidget(self.manual_link_channels)
        row.addStretch(1)
        row.addWidget(play_both)
        row.addWidget(stop)
        al.addLayout(row)
        layout.addWidget(action)

        wave_panel, wl = self.panel("经典 / 自定义波形快捷选择")
        wnote = QLabel(
            "点击波形卡片会选择到 A/B 通道；也可以在上方分别选择不同波形。"
        )
        wnote.setObjectName("muted")
        wnote.setWordWrap(True)
        wl.addWidget(wnote)
        self.manual_wave_grid = QGridLayout()
        self.manual_wave_grid.setSpacing(8)
        wl.addLayout(self.manual_wave_grid)
        layout.addWidget(wave_panel)

        layout.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        self.refresh_manual_wave_buttons()

        a = self.manual_controls[0]
        a["level"].valueChanged.connect(
            lambda v:self.manual_sync_value("level",v)
        )
        a["duration"].valueChanged.connect(
            lambda v:self.manual_sync_value("duration",v)
        )
        a["wave"].currentTextChanged.connect(
            lambda v:self.manual_sync_value("wave",v)
        )

    def refresh_manual_wave_buttons(self):
        if not hasattr(self,"manual_wave_grid"):
            return

        while self.manual_wave_grid.count():
            item=self.manual_wave_grid.takeAt(0)
            w=item.widget()
            if w is not None:
                w.deleteLater()

        names=B.waveform_names()
        cols=4 if self.width()>=1180 else 3 if self.width()>=900 else 2

        for i,name in enumerate(names):
            b=QPushButton(f"〽\n{name}")
            b.setMinimumHeight(72)
            b.clicked.connect(
                lambda checked=False,n=name:
                self.manual_select_waveform(n)
            )
            self.manual_wave_grid.addWidget(
                b,
                i//cols,
                i%cols,
            )

    def manual_sync_value(self,field,value):
        if not self.manual_link_channels.isChecked():
            return

        target=self.manual_controls[1][field]

        if field=="wave":
            if target.currentText()!=value:
                target.setCurrentText(value)
        else:
            if target.value()!=value:
                target.setValue(value)

    def manual_select_waveform(self,name):
        for c in self.manual_controls.values():
            if c["wave"].findText(name)>=0:
                c["wave"].setCurrentText(name)
        self.feedback(f"已选择波形：{name}",2500)

    def manual_send_channel(self,channel):
        c=self.manual_controls[channel]
        ok,msg=B.send_manual_channel(
            channel,
            c["level"].value(),
            c["duration"].value(),
            c["wave"].currentText(),
        )
        if ok:
            duration_value = c[
                "duration"
            ].value()

            self.feedback(
                (
                    f"{'A' if channel==0 else 'B'} 通道"
                    + (
                        "持续会话已启动："
                        if duration_value
                        == B.DURATION_CONTINUOUS
                        else "已发送："
                    )
                    + f"等级 {c['level'].value()} / "
                    + f"{c['wave'].currentText()}"
                ),
                3500,
            )
        else:
            self.msg_warning(
                "手动输出未发送",
                msg,
                "请确认已开启“允许电击输出”，并且 DG APP / 郊狼设备已经接入。",
            )

    def manual_send_both(self):
        a=self.manual_controls[0]
        b=self.manual_controls[1]
        result=B.send_manual_dual(
            a["level"].value(),
            b["level"].value(),
            a["duration"].value(),
            b["duration"].value(),
            a["wave"].currentText(),
            b["wave"].currentText(),
        )
        if result["ok"]:
            has_continuous = (
                a["duration"].value()
                == B.DURATION_CONTINUOUS
                or b["duration"].value()
                == B.DURATION_CONTINUOUS
            )

            self.feedback(
                (
                    "A/B 已发送；-1 通道持续会话已启动。"
                    if has_continuous
                    else "A/B 双通道已发送。"
                ),
                3500,
            )
        else:
            self.msg_warning(
                "双通道输出未完全发送",
                f"A：{result['a'][1]}\nB：{result['b'][1]}",
                "请检查总输出开关、DG APP 和郊狼连接状态。",
            )

    def build_wave(self):
        l = QHBoxLayout(
            self.wave
        )

        a, al = self.panel(
            "我的自定义波形"
        )

        self.wlist = QListWidget()

        self.wlist.currentItemChanged.connect(
            self.wave_selected
        )

        al.addWidget(
            self.wlist,
            1,
        )

        br = QGridLayout()

        nb = QPushButton(
            "新建"
        )

        nb.setObjectName(
            "primary"
        )

        nb.clicked.connect(
            self.new_wave
        )

        db = QPushButton(
            "删除"
        )

        db.clicked.connect(
            self.delete_wave
        )

        import_btn = QPushButton(
            "导入波形 JSON"
        )

        import_btn.clicked.connect(
            self.import_waveforms_json
        )

        export_selected_btn = QPushButton(
            "导出选中波形"
        )

        export_selected_btn.clicked.connect(
            self.export_selected_waveform_json
        )

        export_all_btn = QPushButton(
            "导出全部波形"
        )

        export_all_btn.clicked.connect(
            self.export_all_waveforms_json
        )

        br.addWidget(
            nb,
            0,
            0,
        )

        br.addWidget(
            db,
            0,
            1,
        )

        br.addWidget(
            import_btn,
            1,
            0,
            1,
            2,
        )

        br.addWidget(
            export_selected_btn,
            2,
            0,
        )

        br.addWidget(
            export_all_btn,
            2,
            1,
        )

        al.addLayout(
            br
        )

        b, bl = self.panel(
            "波形编辑器"
        )

        bl.addWidget(
            QLabel(
                "波形名称"
            )
        )

        self.wname = QLineEdit()

        bl.addWidget(
            self.wname
        )

        note = QLabel(
            "一行一个 16 位十六进制 HEX 帧，也支持逗号分隔。最多 512 帧。"
        )

        note.setObjectName(
            "muted"
        )

        bl.addWidget(
            note
        )

        self.wedit = QTextEdit()

        self.wedit.setFont(
            QFont(
                "Consolas",
                10,
            )
        )

        bl.addWidget(
            self.wedit,
            1,
        )

        self.winfo = QLabel(
            "尚未选择波形"
        )

        self.winfo.setObjectName(
            "muted"
        )

        bl.addWidget(
            self.winfo
        )

        bot = QHBoxLayout()

        bot.addStretch()

        vb = QPushButton(
            "校验"
        )

        vb.clicked.connect(
            lambda:
            self.validate_wave(
                True
            )
        )

        sb = QPushButton(
            "保存 / 更新"
        )

        sb.setObjectName(
            "primary"
        )

        sb.clicked.connect(
            self.save_wave
        )

        bot.addWidget(
            vb
        )

        bot.addWidget(
            sb
        )

        bl.addLayout(
            bot
        )

        l.addWidget(
            a,
            1,
        )

        l.addWidget(
            b,
            3,
        )

    def waveform_export_payload(
        self,
        waveforms,
    ):
        return {
            "format": "coyote-waveforms-v1",
            "version": 1,
            "waveforms": waveforms,
        }

    def export_selected_waveform_json(
        self,
    ):
        item = self.wlist.currentItem()

        if item is None:
            self.msg_warning(
                "无法导出",
                "请先在左侧选择一个自定义波形。",
            )
            return

        name = item.text()

        frames = B.custom_waveforms.get(
            name
        )

        if not frames:
            self.msg_error(
                "无法导出",
                "当前波形不存在或没有有效帧。",
                name,
            )
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出选中波形",
            str(
                B.ROOT
                / f"{name}.json"
            ),
            "JSON (*.json)",
        )

        if not path:
            self.feedback(
                "已取消导出波形。",
                2500,
            )
            return

        if not path.lower().endswith(
            ".json"
        ):
            path += ".json"

        try:
            payload = (
                self.waveform_export_payload({
                    name: list(frames)
                })
            )

            Path(path).write_text(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            self.msg_info(
                "波形导出成功",
                f"“{name}”已经导出为 JSON。",
                f"文件：{path}",
            )

        except Exception as e:
            self.msg_error(
                "波形导出失败",
                "无法写入 JSON 文件。",
                f"{type(e).__name__}: {e}",
            )

    def export_all_waveforms_json(
        self,
    ):
        if not B.custom_waveforms:
            self.msg_warning(
                "没有可导出的波形",
                "当前还没有任何自定义波形。",
            )
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出全部自定义波形",
            str(
                B.ROOT
                / "coyote_waveforms.json"
            ),
            "JSON (*.json)",
        )

        if not path:
            self.feedback(
                "已取消导出波形。",
                2500,
            )
            return

        if not path.lower().endswith(
            ".json"
        ):
            path += ".json"

        try:
            payload = (
                self.waveform_export_payload(
                    json.loads(
                        json.dumps(
                            B.custom_waveforms,
                            ensure_ascii=False,
                        )
                    )
                )
            )

            Path(path).write_text(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            self.msg_info(
                "全部波形导出成功",
                f"已导出 {len(B.custom_waveforms)} 个自定义波形。",
                f"文件：{path}",
            )

        except Exception as e:
            self.msg_error(
                "波形导出失败",
                "无法写入 JSON 文件。",
                f"{type(e).__name__}: {e}",
            )

    def parse_waveform_import_payload(
        self,
        data,
    ):
        if not isinstance(
            data,
            dict,
        ):
            raise ValueError(
                "JSON 顶层必须是对象。"
            )

        # 标准格式：
        # {
        #   "format":"coyote-waveforms-v1",
        #   "waveforms":{"名字":["HEX", ...]}
        # }
        if "waveforms" in data:
            fmt = data.get(
                "format"
            )

            if fmt not in (
                None,
                "coyote-waveforms-v1",
            ):
                raise ValueError(
                    f"不支持的波形格式：{fmt}"
                )

            source = data.get(
                "waveforms"
            )

        # 同时兼容单波形格式：
        # {"name":"xxx","frames":[...]}
        elif (
            "name" in data
            and "frames" in data
        ):
            source = {
                str(
                    data[
                        "name"
                    ]
                ):
                data[
                    "frames"
                ]
            }

        # 也兼容直接 mapping：
        # {"Wave1":["..."],"Wave2":["..."]}
        else:
            source = data

        if not isinstance(
            source,
            dict,
        ):
            raise ValueError(
                "waveforms 必须是 JSON 对象。"
            )

        valid = {}
        errors = []
        conflicts = []

        for raw_name, raw_frames in (
            source.items()
        ):
            ok_name, name_or_error = (
                B.validate_waveform_name(
                    raw_name
                )
            )

            if not ok_name:
                errors.append(
                    f"{raw_name}: {name_or_error}"
                )
                continue

            name = name_or_error

            if name in B.BUILTIN_WAVEFORM_NAMES:
                errors.append(
                    f"{name}: 内置波形不能覆盖"
                )
                continue

            if not isinstance(
                raw_frames,
                list,
            ):
                errors.append(
                    f"{name}: frames 必须是数组"
                )
                continue

            frames = []

            invalid = None

            for index, raw_frame in enumerate(
                raw_frames,
                start=1,
            ):
                frame = (
                    B.normalize_waveform_frame(
                        raw_frame
                    )
                )

                if frame is None:
                    invalid = (
                        f"{name}: 第 {index} 帧格式错误"
                    )
                    break

                frames.append(
                    frame
                )

            if invalid:
                errors.append(
                    invalid
                )
                continue

            if not frames:
                errors.append(
                    f"{name}: 至少需要 1 帧"
                )
                continue

            if len(frames) > 512:
                errors.append(
                    f"{name}: 超过 512 帧"
                )
                continue

            valid[
                name
            ] = frames

            if name in B.custom_waveforms:
                conflicts.append(
                    name
                )

        return (
            valid,
            errors,
            conflicts,
        )

    def import_waveforms_json(
        self,
    ):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入波形 JSON",
            str(B.ROOT),
            "JSON (*.json)",
        )

        if not path:
            self.feedback(
                "已取消导入波形。",
                2500,
            )
            return

        try:
            data = json.loads(
                Path(path).read_text(
                    encoding="utf-8"
                )
            )

            valid, errors, conflicts = (
                self.parse_waveform_import_payload(
                    data
                )
            )

            if not valid:
                detail = (
                    "\n".join(
                        errors[:20]
                    )
                    or "没有检测到可导入的有效波形。"
                )

                self.msg_error(
                    "波形导入失败",
                    "JSON 中没有可导入的有效自定义波形。",
                    detail,
                )
                return

            if conflicts:
                shown = ", ".join(
                    conflicts[:12]
                )

                if len(conflicts) > 12:
                    shown += (
                        f" 等 {len(conflicts)} 个"
                    )

                if not self.ask_confirm(
                    "覆盖已有波形",
                    "导入文件中有同名自定义波形。",
                    (
                        f"将覆盖：{shown}\n"
                        "内置波形不会被覆盖。"
                    ),
                    confirm_text="覆盖并导入",
                    cancel_text="取消",
                ):
                    return

            for name, frames in (
                valid.items()
            ):
                B.custom_waveforms[
                    name
                ] = list(
                    frames
                )

                B.COYOTE_WAVEFORMS[
                    name
                ] = list(
                    frames
                )

            self.refresh_wave_lists()
            self.load_rules()

            ok_save, save_message = (
                save_full_config()
            )

            detail_lines = [
                f"成功：{len(valid)} 个"
            ]

            if errors:
                detail_lines.append(
                    f"跳过无效项：{len(errors)} 个"
                )

                detail_lines.append(
                    "\n".join(
                        errors[:10]
                    )
                )

            if ok_save:
                self.msg_info(
                    "波形导入完成",
                    f"已导入 {len(valid)} 个自定义波形。",
                    "\n".join(
                        detail_lines
                    ),
                )
            else:
                self.msg_warning(
                    "波形已导入，但配置保存失败",
                    f"当前运行时已加入 {len(valid)} 个波形。",
                    save_message,
                )

        except Exception as e:
            self.msg_error(
                "波形导入失败",
                "无法读取这个 JSON 文件。",
                f"{type(e).__name__}: {e}",
            )

    def refresh_wave_lists(self):
        cur=self.selected_wave;self.wlist.blockSignals(True);self.wlist.clear();names=sorted(B.custom_waveforms,key=str.casefold);row=-1
        for i,n in enumerate(names):self.wlist.addItem(n);row=i if n==cur else row
        self.wlist.blockSignals(False)
        if row>=0:self.wlist.setCurrentRow(row)
        for e in self.rule_editors.values():
            e.refresh_waveforms()

        if hasattr(self,"manual_controls"):
            values=B.waveform_names()
            for controls in self.manual_controls.values():
                current=controls["wave"].currentText()
                controls["wave"].clear()
                controls["wave"].addItems(values)
                if current in values:
                    controls["wave"].setCurrentText(current)
            self.refresh_manual_wave_buttons()

    def new_wave(self):self.selected_wave=None;self.wname.clear();self.wedit.setPlainText("2D2D2D2D00000000\n2D2D2D2D64646464");self.winfo.setText("新建波形")
    def wave_selected(self,current,previous):
        if not current:return
        n=current.text();frames=B.custom_waveforms.get(n)
        if not frames:return
        self.selected_wave=n;self.wname.setText(n);self.wedit.setPlainText("\n".join(frames));self.winfo.setText(f"{n} · {len(frames)} 帧")
    def validate_wave(self,show=True):
        ok,n=B.validate_waveform_name(self.wname.text())
        if not ok:
            if show:
                self.msg_error(
                    "波形名称无效",
                    "无法使用这个波形名称。",
                    n,
                )
            return False,n,None
        if n in B.BUILTIN_WAVEFORM_NAMES:
            msg=f"“{n}”是内置波形，不能通过自定义编辑器覆盖。"
            if show:
                self.msg_error(
                    "波形名称冲突",
                    msg,
                    "请使用一个新的自定义名称。",
                )
            return False,msg,None
        ok,fr=B.parse_waveform_text(self.wedit.toPlainText())
        if not ok:
            if show:
                self.msg_error(
                    "波形数据格式错误",
                    "自定义波形没有通过校验。",
                    fr,
                )
            return False,fr,None
        msg=f"校验通过：{n} · {len(fr)} 帧";self.winfo.setText(msg)
        if show:
            self.msg_info(
                "波形校验通过",
                msg,
                "现在可以点击“保存 / 更新”把波形写入配置文件。",
            )
        return True,n,fr

    def replace_wave_refs(self,old,new):
        with B.rule_lock:
            for c in B.rules.values():
                if c.get("waveform_a")==old:c["waveform_a"]=new
                if c.get("waveform_b")==old:c["waveform_b"]=new
                for t in c.get("thresholds",[]):
                    if t.get("waveform_a")==old:t["waveform_a"]=new
                    if t.get("waveform_b")==old:t["waveform_b"]=new

    def save_wave(self):
        ok,n,fr=self.validate_wave(False)
        if not ok:
            self.msg_error(
                "波形保存失败",
                "无法保存当前自定义波形。",
                n,
            )
            return
        old=self.selected_wave
        if old and old!=n and old in B.custom_waveforms:B.custom_waveforms.pop(old,None);B.COYOTE_WAVEFORMS.pop(old,None);self.replace_wave_refs(old,n)
        B.custom_waveforms[n] = list(fr)
        B.COYOTE_WAVEFORMS[n] = list(fr)

        self.selected_wave = n

        self.refresh_wave_lists()
        self.load_rules()

        ok_save, save_message = (
            save_full_config()
        )

        self.winfo.setText(
            f"已保存：{n} · {len(fr)} 帧"
        )

        if ok_save:
            B.add_log(
                "系统",
                "自定义波形已保存",
                f"{n} · {len(fr)} 帧",
            )

            self.msg_info(
                "波形保存成功",
                f"自定义波形“{n}”已经保存。",
                f"共 {len(fr)} 帧；现在可以在基础波形和动态档位中直接选择。",
            )
        else:
            self.msg_error(
                "波形配置保存失败",
                "波形已经加入当前运行时，但无法写入配置文件。",
                save_message,
            )

    def delete_wave(self):
        it=self.wlist.currentItem()
        if not it:return
        n=it.text()
        if not self.ask_confirm(
            "删除自定义波形",
            f"确定删除“{n}”吗？",
            "正在引用这个波形的基础规则会回退为“脉冲”，动态档位会回退为“沿用基础波形”。",
            confirm_text="删除",
            cancel_text="取消",
        ):
            return
        B.custom_waveforms.pop(n,None);B.COYOTE_WAVEFORMS.pop(n,None)
        with B.rule_lock:
            for c in B.rules.values():
                if c.get("waveform_a")==n:c["waveform_a"]="脉冲"
                if c.get("waveform_b")==n:c["waveform_b"]="脉冲"
                for t in c.get("thresholds",[]):
                    if t.get("waveform_a")==n:t["waveform_a"]=B.TIER_WAVEFORM_INHERIT
                    if t.get("waveform_b")==n:t["waveform_b"]=B.TIER_WAVEFORM_INHERIT
        self.selected_wave = None
        self.wname.clear()
        self.wedit.clear()
        self.refresh_wave_lists()
        self.load_rules()

        ok_save, save_message = (
            save_full_config()
        )

        if ok_save:
            self.msg_info(
                "删除完成",
                f"自定义波形“{n}”已经删除。",
                "相关规则引用已自动回退到有效波形。",
            )
        else:
            self.msg_error(
                "删除后保存失败",
                "波形已经从当前运行时删除，但配置文件更新失败。",
                save_message,
            )

    def slider(self,layout,title,lo,hi,val):
        box=QVBoxLayout();top=QHBoxLayout();lab=QLabel(title);v=QLabel(str(val));top.addWidget(lab);top.addStretch();top.addWidget(v);s=QSlider(Qt.Orientation.Horizontal);s.setRange(lo,hi);s.setValue(val);s.valueChanged.connect(lambda x:v.setText(str(x)));box.addLayout(top);box.addWidget(s);layout.addLayout(box);return s

    # --------------------------------------------------------
    # 自定义 Python 编程
    # --------------------------------------------------------

    def build_custom_code(self):
        B.ensure_custom_rule_assets()

        outer = QVBoxLayout(
            self.code_page
        )
        outer.setContentsMargins(
            4, 4, 4, 4
        )
        outer.setSpacing(
            10
        )

        top, tl = self.panel(
            "自定义 Python 规则"
        )

        intro = QLabel(
            "把 .py 规则放进 custom_rules 后点击重新加载。"
            "脚本负责判断游戏条件，真正输出仍统一经过 Coyote backend 的总开关和硬限制。"
        )
        intro.setWordWrap(
            True
        )
        intro.setObjectName(
            "muted"
        )
        tl.addWidget(
            intro
        )

        self.custom_rule_path_label = QLabel(
            str(
                B.CUSTOM_RULE_DIR
            )
        )
        self.custom_rule_path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.custom_rule_path_label.setWordWrap(
            True
        )
        tl.addWidget(
            self.custom_rule_path_label
        )

        buttons = QHBoxLayout()

        reload_btn = QPushButton(
            "重新加载脚本"
        )
        reload_btn.setObjectName(
            "primary"
        )
        reload_btn.clicked.connect(
            self.reload_custom_rules
        )

        import_btn = QPushButton(
            "导入 .py"
        )
        import_btn.clicked.connect(
            self.import_custom_rule
        )

        example_btn = QPushButton(
            "创建 / 恢复示例"
        )
        example_btn.clicked.connect(
            self.create_custom_rule_example
        )

        open_btn = QPushButton(
            "打开脚本目录"
        )
        open_btn.clicked.connect(
            self.open_custom_rule_folder
        )

        selected_btn = QPushButton(
            "打开选中脚本"
        )
        selected_btn.clicked.connect(
            self.open_selected_custom_rule
        )

        for button in (
            reload_btn,
            import_btn,
            example_btn,
            open_btn,
            selected_btn,
        ):
            buttons.addWidget(
                button
            )

        buttons.addStretch(
            1
        )
        tl.addLayout(
            buttons
        )

        outer.addWidget(
            top
        )

        split = QSplitter(
            Qt.Orientation.Vertical
        )
        split.setChildrenCollapsible(
            False
        )

        list_panel, list_layout = self.panel(
            "已加载脚本"
        )

        self.custom_rule_table = QTableWidget(
            0,
            6,
        )

        self.setup_table(
            self.custom_rule_table,
            [
                "文件",
                "名称",
                "启用",
                "模式",
                "冷却",
                "状态 / 错误",
            ],
        )

        self.custom_rule_table.horizontalHeader().setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.custom_rule_table.horizontalHeader().setSectionResizeMode(
            2,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.custom_rule_table.horizontalHeader().setSectionResizeMode(
            3,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.custom_rule_table.horizontalHeader().setSectionResizeMode(
            4,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.custom_rule_table.cellDoubleClicked.connect(
            lambda row, column:
            self.open_custom_rule_row(
                row
            )
        )

        list_layout.addWidget(
            self.custom_rule_table
        )
        split.addWidget(
            list_panel
        )

        docs_panel, docs_layout = self.panel(
            "内置文档"
        )

        self.custom_docs_tabs = QTabWidget()

        self.custom_rule_guide = QTextEdit()
        self.custom_rule_guide.setReadOnly(
            True
        )

        self.app_intro_doc = QTextEdit()
        self.app_intro_doc.setReadOnly(
            True
        )

        self.custom_docs_tabs.addTab(
            self.custom_rule_guide,
            "自定义规则开发指南",
        )
        self.custom_docs_tabs.addTab(
            self.app_intro_doc,
            "软件介绍",
        )

        docs_layout.addWidget(
            self.custom_docs_tabs
        )
        split.addWidget(
            docs_panel
        )

        split.setSizes(
            [330, 520]
        )

        outer.addWidget(
            split,
            1,
        )

        self.reload_custom_rule_docs()
        self.refresh_custom_rule_table()

    def reload_custom_rule_docs(self):
        B.ensure_custom_rule_assets()

        try:
            guide = B.CUSTOM_RULE_DOC_FILE.read_text(
                encoding="utf-8",
            )
        except Exception as e:
            guide = (
                "读取开发文档失败："
                + str(e)
            )

        try:
            intro = B.APP_INTRO_DOC_FILE.read_text(
                encoding="utf-8",
            )
        except Exception as e:
            intro = (
                "读取软件介绍失败："
                + str(e)
            )

        # Qt 6 QTextEdit 支持 Markdown。
        try:
            self.custom_rule_guide.setMarkdown(
                guide
            )
            self.app_intro_doc.setMarkdown(
                intro
            )
        except Exception:
            self.custom_rule_guide.setPlainText(
                guide
            )
            self.app_intro_doc.setPlainText(
                intro
            )

    def refresh_custom_rule_table(self):
        if not hasattr(
            self,
            "custom_rule_table",
        ):
            return

        items = B.custom_rule_statuses()

        self.custom_rule_table.setRowCount(
            len(items)
        )

        for row, item in enumerate(
            items
        ):
            error = str(
                item.get(
                    "error",
                    "",
                )
                or ""
            )

            values = (
                item.get(
                    "file",
                    "",
                ),
                item.get(
                    "name",
                    "",
                ),
                (
                    "是"
                    if item.get(
                        "enabled",
                        False,
                    )
                    else "否"
                ),
                (
                    "持续"
                    if item.get(
                        "mode"
                    )
                    == "while"
                    else "边沿"
                ),
                (
                    f"{float(item.get('cooldown', 0)):.1f} s"
                ),
                (
                    error
                    if error
                    else "已加载"
                ),
            )

            for column, value in enumerate(
                values
            ):
                cell = QTableWidgetItem(
                    str(value)
                )

                if column == 0:
                    cell.setData(
                        Qt.ItemDataRole.UserRole,
                        item.get(
                            "path",
                            "",
                        ),
                    )

                self.custom_rule_table.setItem(
                    row,
                    column,
                    cell,
                )

        self.custom_rule_table.resizeRowsToContents()

    def reload_custom_rules(self):
        count, errors = B.load_custom_rules()

        self.refresh_custom_rule_table()
        self.reload_custom_rule_docs()

        self.feedback(
            (
                f"自定义规则已重新加载："
                f"{count} 个脚本，"
                f"{errors} 个错误。"
            ),
            5000,
        )

    def open_custom_rule_folder(self):
        B.ensure_custom_rule_assets()

        try:
            if sys.platform.startswith(
                "win"
            ):
                os.startfile(
                    str(
                        B.CUSTOM_RULE_DIR
                    )
                )
            else:
                self.feedback(
                    str(
                        B.CUSTOM_RULE_DIR
                    ),
                    5000,
                )

        except Exception as e:
            self.msg_error(
                "打开目录失败",
                "无法打开 custom_rules。",
                str(e),
            )

    def import_custom_rule(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入 Coyote 自定义 Python 规则",
            "",
            "Python (*.py)",
        )

        if not path:
            return

        source = Path(
            path
        )

        B.ensure_custom_rule_assets()

        target = (
            B.CUSTOM_RULE_DIR
            / source.name
        )

        if target.exists():
            if not self.ask_confirm(
                "脚本已存在",
                f"{source.name} 已经存在。",
                "是否覆盖并重新加载？",
                confirm_text="覆盖",
                cancel_text="取消",
            ):
                return

        try:
            shutil.copy2(
                source,
                target,
            )
        except Exception as e:
            self.msg_error(
                "导入失败",
                "无法复制 Python 脚本。",
                str(e),
            )
            return

        self.reload_custom_rules()

        self.feedback(
            (
                "已导入："
                + str(
                    target
                )
            ),
            5000,
        )

    def create_custom_rule_example(self):
        B.ensure_custom_rule_assets()

        if B.CUSTOM_RULE_EXAMPLE_FILE.exists():
            if not self.ask_confirm(
                "恢复示例",
                "示例脚本已经存在。",
                "是否用内置示例覆盖它？",
                confirm_text="覆盖示例",
                cancel_text="取消",
            ):
                return

            ok, path = B.write_custom_rule_example(
                overwrite=True
            )

        else:
            ok, path = B.write_custom_rule_example(
                overwrite=False
            )

        self.reload_custom_rules()

        self.feedback(
            (
                "示例已写入："
                + path
            ),
            5000,
        )

    def open_custom_rule_row(
        self,
        row,
    ):
        item = self.custom_rule_table.item(
            row,
            0,
        )

        if item is None:
            return

        path = str(
            item.data(
                Qt.ItemDataRole.UserRole
            )
            or ""
        )

        if not path:
            return

        try:
            if sys.platform.startswith(
                "win"
            ):
                os.startfile(
                    path
                )
            else:
                self.feedback(
                    path,
                    5000,
                )

        except Exception as e:
            self.msg_error(
                "打开脚本失败",
                "无法打开选中的 Python 文件。",
                str(e),
            )

    def open_selected_custom_rule(self):
        row = self.custom_rule_table.currentRow()

        if row < 0:
            self.feedback(
                "请先在表格中选择一个脚本。",
                3000,
            )
            return

        self.open_custom_rule_row(
            row
        )


    def build_appearance(self):
        outer=QVBoxLayout(self.look); outer.setContentsMargins(4,4,4,4)
        scroll=QScrollArea(); scroll.setWidgetResizable(True)
        body=QWidget(); l=QVBoxLayout(body); l.setSpacing(12)

        language_title = QLabel(
            "语言"
        )
        language_title.setStyleSheet(
            "font-size:18px;font-weight:700"
        )
        l.addWidget(
            language_title
        )

        language_panel, language_layout = self.panel(
            "界面语言"
        )

        language_info = QLabel(
            "界面支持简体中文、繁體中文、English、日本語。"
            "翻译文件位于 src/Coyote/language，可直接编辑后重新加载。"
        )
        language_info.setObjectName(
            "muted"
        )
        language_info.setWordWrap(
            True
        )
        language_layout.addWidget(
            language_info
        )

        language_form = QFormLayout()

        self.language_combo = QComboBox()
        self.language_combo.setProperty(
            "i18n_skip",
            True,
        )

        for code in I18N.available_languages():
            self.language_combo.addItem(
                I18N.language_name(
                    code
                ),
                code,
            )

        language_index = (
            self.language_combo.findData(
                I18N.get_language()
            )
        )

        if language_index >= 0:
            self.language_combo.setCurrentIndex(
                language_index
            )

        language_form.addRow(
            "界面语言",
            self.language_combo,
        )

        language_layout.addLayout(
            language_form
        )

        language_buttons = QHBoxLayout()

        reload_language_button = QPushButton(
            "重新加载语言文件"
        )
        reload_language_button.clicked.connect(
            self.reload_language_files
        )

        open_language_button = QPushButton(
            "打开语言文件夹"
        )
        open_language_button.clicked.connect(
            self.open_locale_folder
        )

        language_buttons.addWidget(
            reload_language_button
        )
        language_buttons.addWidget(
            open_language_button
        )
        language_buttons.addStretch(
            1
        )

        language_layout.addLayout(
            language_buttons
        )

        l.addWidget(
            language_panel
        )

        self.language_combo.currentIndexChanged.connect(
            self.language_changed
        )

        title=QLabel("外观与背景"); title.setStyleSheet("font-size:18px;font-weight:700"); l.addWidget(title)
        row=QGridLayout()
        a,al=self.panel("背景图片")
        self.bgpath=QLabel(appearance.get("background_image") or "未选择背景"); self.bgpath.setWordWrap(True); al.addWidget(self.bgpath)
        choose=QPushButton("选择背景图片"); choose.setObjectName("primary"); choose.clicked.connect(self.choose_background)
        clear=QPushButton("恢复纯色背景"); clear.clicked.connect(self.clear_background)
        al.addWidget(choose); al.addWidget(clear)
        self.bgen=QCheckBox("启用背景图片"); self.bgen.setChecked(bool(appearance["background_enabled"])); al.addWidget(self.bgen)
        self.bgop=self.slider(al,"背景可见度",0,100,int(float(appearance["background_opacity"])*100))
        self.bgblur=self.slider(al,"背景模糊 / 毛玻璃强度",0,40,int(appearance["background_blur"]))
        self.bgbright=self.slider(al,"背景亮度",15,150,int(float(appearance["background_brightness"])*100))
        f=QFormLayout(); self.bgfit=QComboBox(); self.bgfit.addItems(["cover","contain","stretch"]); self.bgfit.setCurrentText(str(appearance["background_fit"])); f.addRow("背景缩放",self.bgfit); al.addLayout(f)

        b,bl=self.panel("毛玻璃 / 数据显示")
        self.glassop=self.slider(bl,"毛玻璃卡片透明度",15,95,int(float(appearance["glass_opacity"])*100))
        self.radius=self.slider(bl,"卡片圆角",0,32,int(appearance["glass_radius"]))
        self.borderop=self.slider(bl,"玻璃边框透明度",0,100,int(float(appearance["glass_border_opacity"])*100))
        self.showvals=QCheckBox("显示状态百分比数值"); self.showvals.setChecked(bool(appearance["show_values"]))
        self.showbar=QCheckBox("显示状态进度条"); self.showbar.setChecked(bool(appearance["show_progress"]))
        self.compact=QCheckBox("紧凑状态卡（隐藏按钮）"); self.compact.setChecked(bool(appearance["compact_status_cards"]))
        bl.addWidget(self.showvals); bl.addWidget(self.showbar); bl.addWidget(self.compact)
        form=QFormLayout(); self.dec=QSpinBox(); self.dec.setRange(0,2); self.dec.setValue(int(appearance["status_decimals"])); form.addRow("百分比小数位",self.dec); bl.addLayout(form)
        colors=QHBoxLayout(); cc=QPushButton("选择强调色"); cc.clicked.connect(self.choose_accent); colors.addWidget(cc)
        for n,c in (("蓝","#5B8CFF"),("紫","#8B5CF6"),("青","#22D3EE"),("绿","#34D399"),("橙","#F59E0B")):
            bt=QPushButton(n); bt.clicked.connect(lambda checked=False,x=c:self.set_accent(x)); colors.addWidget(bt)
        bl.addLayout(colors)
        sv=QPushButton("保存外观设置"); sv.setObjectName("primary"); sv.clicked.connect(self.save_appearance); bl.addWidget(sv)
        row.addWidget(a,0,0); row.addWidget(b,0,1); row.setColumnStretch(0,1); row.setColumnStretch(1,1); l.addLayout(row)

        net_title=QLabel("连接与端口"); net_title.setStyleSheet("font-size:18px;font-weight:700"); l.addWidget(net_title)
        net, nl=self.panel("PEAK / DG-LAB 端口")
        info=QLabel("默认：PEAK UDP 8765；DG-LAB V4 9998。可以修改，保存后建议重启控制器；PEAK 插件端口同步后建议同时重启 PEAK。")
        info.setObjectName("muted"); info.setWordWrap(True); nl.addWidget(info)
        nf=QFormLayout()
        self.peak_port_spin=QSpinBox(); self.peak_port_spin.setRange(1024,65535); self.peak_port_spin.setValue(int(B.network_settings.get("peak_port",B.DEFAULT_PEAK_PORT)))
        self.dg_port_spin=QSpinBox(); self.dg_port_spin.setRange(1024,65535); self.dg_port_spin.setValue(int(B.network_settings.get("dg_port",B.DEFAULT_DG_PORT)))
        self.game_dir_edit=QLineEdit(str(B.network_settings.get("peak_game_dir","") or "")); self.game_dir_edit.setPlaceholderText("PEAK 游戏目录，例如 D:\\steam\\steamapps\\common\\PEAK")
        nf.addRow("PEAK 游戏 UDP 端口",self.peak_port_spin); nf.addRow("DG-LAB V4 端口",self.dg_port_spin); nf.addRow("PEAK 游戏目录",self.game_dir_edit); nl.addLayout(nf)
        nr=QHBoxLayout()
        detect=QPushButton("自动检测 PEAK 目录"); detect.clicked.connect(self.detect_peak_dir)
        browse=QPushButton("浏览目录"); browse.clicked.connect(self.browse_peak_dir)
        save_net=QPushButton("保存端口并同步插件"); save_net.setObjectName("primary"); save_net.clicked.connect(self.save_network_settings)
        nr.addWidget(detect); nr.addWidget(browse); nr.addStretch(1); nr.addWidget(save_net); nl.addLayout(nr)
        self.network_status=QLabel(f"当前本次运行监听：PEAK {B.PEAK_PORT} / DG {B.DG_PORT}"); self.network_status.setObjectName("muted"); nl.addWidget(self.network_status)
        l.addWidget(net)

        # -------------------- BepInEx 管理 --------------------
        bepinex_title=QLabel("BepInEx 管理")
        bepinex_title.setStyleSheet("font-size:18px;font-weight:700")
        l.addWidget(bepinex_title)

        bp,bpl=self.panel("PEAK BepInEx 一键安装 / 修复")

        bp_info=QLabel(
            f"PEAK 专用包：BepInExPack_PEAK {B.BEPINEX_PEAK_PACKAGE_VERSION} "
            f"(BepInEx {B.BEPINEX_CORE_VERSION})。"
            "无需 Mod Manager；程序会直接安装到 PEAK 游戏根目录。"
        )
        bp_info.setObjectName("muted")
        bp_info.setWordWrap(True)
        bpl.addWidget(bp_info)

        self.bepinex_status_label=QLabel("尚未检测")
        self.bepinex_status_label.setWordWrap(True)
        bpl.addWidget(self.bepinex_status_label)

        self.bepinex_progress_bar=QProgressBar()
        self.bepinex_progress_bar.setRange(0,0)
        self.bepinex_progress_bar.hide()
        bpl.addWidget(self.bepinex_progress_bar)

        bp_buttons=QGridLayout()

        self.bepinex_check_btn=QPushButton("检测 BepInEx")
        self.bepinex_check_btn.clicked.connect(self.refresh_bepinex_status)

        self.bepinex_install_btn=QPushButton("一键下载安装 BepInEx")
        self.bepinex_install_btn.setObjectName("primary")
        self.bepinex_install_btn.clicked.connect(self.install_bepinex_online)

        self.bepinex_local_btn=QPushButton("从本地 ZIP 安装")
        self.bepinex_local_btn.clicked.connect(self.install_bepinex_local)

        self.coyote_install_btn=QPushButton("安装 / 更新 Coyote.dll")
        self.coyote_install_btn.clicked.connect(self.install_coyote_dll_to_peak)

        self.open_plugins_btn=QPushButton("打开 BepInEx/plugins")
        self.open_plugins_btn.clicked.connect(self.open_peak_plugins_folder)

        bp_buttons.addWidget(self.bepinex_check_btn,0,0)
        bp_buttons.addWidget(self.bepinex_install_btn,0,1)
        bp_buttons.addWidget(self.bepinex_local_btn,1,0)
        bp_buttons.addWidget(self.coyote_install_btn,1,1)
        bp_buttons.addWidget(self.open_plugins_btn,2,0,1,2)

        bpl.addLayout(bp_buttons)

        backup_note=QLabel(
            "安装/修复不会删除现有 plugins。将被覆盖的 BepInEx 文件会先备份到 "
            "PEAK\\CoyoteBackups。安装前必须完全退出 PEAK。"
        )
        backup_note.setObjectName("muted")
        backup_note.setWordWrap(True)
        bpl.addWidget(backup_note)

        l.addWidget(bp)

        l.addStretch(1)
        scroll.setWidget(body); outer.addWidget(scroll)

        # Background enable / fit are discrete operations, so apply directly.
        self.bgen.stateChanged.connect(
            self.background_fast_changed
        )
        self.bgfit.currentTextChanged.connect(
            self.background_fast_changed
        )

        # All visual-effect sliders use lightweight drag mode.
        # During drag: only slider thumb + numeric label move.
        # On release: apply one final render/style update.
        for slider in (
            self.bgop,
            self.bgblur,
            self.bgbright,
            self.glassop,
            self.radius,
            self.borderop,
        ):
            slider.sliderPressed.connect(
                self._appearance_drag_started
            )

        for slider in (
            self.bgop,
            self.bgblur,
            self.bgbright,
        ):
            slider.sliderReleased.connect(
                self.background_heavy_changed
            )

        for slider in (
            self.glassop,
            self.radius,
            self.borderop,
        ):
            slider.sliderReleased.connect(
                self.theme_style_changed
            )

        # 数据显示选项只更新状态卡。
        for box in (self.showvals,self.showbar,self.compact):
            box.stateChanged.connect(self.status_display_changed)
        self.dec.valueChanged.connect(self.status_display_changed)

    def detect_peak_dir(self):
        self.feedback(
            "正在检测 Steam 库和常见 PEAK 安装路径……",
            3000,
        )
        path=B.get_peak_game_dir()
        if path:
            self.game_dir_edit.setText(str(path))
            B.network_settings["peak_game_dir"]=str(path)
            self.feedback(f"已检测到 PEAK：{path}",5000)
            self.refresh_bepinex_status()
        else:
            self.msg_warning(
                "未检测到 PEAK 目录",
                "自动检测没有找到 PEAK.exe。",
                "现在会检查 Steam 注册表、libraryfolders.vdf、appmanifest，以及 C:~Z: 下常见 steam / SteamLibrary 路径。\n仍未找到时可以手动浏览目录。",
            )

    def browse_peak_dir(self):
        path=QFileDialog.getExistingDirectory(self,"选择 PEAK 游戏目录",self.game_dir_edit.text() or str(B.ROOT))
        if path:
            self.game_dir_edit.setText(path)

    def save_network_settings(self):
        peak_port=int(self.peak_port_spin.value()); dg_port=int(self.dg_port_spin.value())
        if peak_port==dg_port:
            self.msg_error("端口冲突","PEAK UDP 和 DG-LAB WebSocket 不能使用同一个端口。",f"当前都设置为 {peak_port}。")
            return
        B.network_settings["peak_port"]=peak_port
        B.network_settings["dg_port"]=dg_port
        B.network_settings["peak_game_dir"]=self.game_dir_edit.text().strip()
        ok_plugin, plugin_message=B.write_peak_plugin_network_config(B.network_settings["peak_game_dir"] or None,peak_port)
        ok_save, save_message=save_full_config()
        if not ok_save:
            self.msg_error("端口保存失败","无法写入控制器配置文件。",save_message); return
        detail=(f"已保存：PEAK UDP {peak_port} / DG-LAB V4 {dg_port}.\n"
                f"本次运行仍使用：PEAK {B.PEAK_PORT} / DG {B.DG_PORT}.\n"
                "关闭并重新启动本控制器后，Python 监听和 DG Server 使用新端口。")
        if ok_plugin:
            detail += f"\nPEAK 插件配置已写入：{plugin_message}\n建议同步重启 PEAK。"
        else:
            detail += f"\nPEAK 插件端口尚未同步：{plugin_message}\n可设置正确游戏目录后再次保存。"
        self.msg_info("端口设置已保存","新的连接端口已经保存。",detail)
        self.refresh_bepinex_status()

    # --------------------------------------------------------
    # BepInEx 管理
    # --------------------------------------------------------

    def current_peak_dir_for_install(self):
        raw = ""

        if hasattr(
            self,
            "game_dir_edit",
        ):
            raw = (
                self.game_dir_edit.text()
                .strip()
            )

        if raw:
            return Path(raw)

        detected = B.get_peak_game_dir()

        if detected is not None:
            if hasattr(
                self,
                "game_dir_edit",
            ):
                self.game_dir_edit.setText(
                    str(detected)
                )

            return Path(detected)

        return None

    def refresh_bepinex_status(self):
        game_dir = (
            self.current_peak_dir_for_install()
        )

        status = B.get_bepinex_status(
            game_dir
        )

        if not hasattr(
            self,
            "bepinex_status_label",
        ):
            return

        if not status.get(
            "valid_game",
            False,
        ):
            self.bepinex_status_label.setText(
                "○ 未找到有效 PEAK 目录\n"
                + status.get(
                    "message",
                    ""
                )
            )
            self.bepinex_status_label.setStyleSheet(
                "color:#F5B84B;font-weight:700"
            )
            return

        if status.get(
            "complete",
            False,
        ):
            if status.get(
                "coyote_plugin_installed"
            ):
                installed_name = status.get(
                    "coyote_plugin_filename",
                    B.COYOTE_PLUGIN_FILENAME,
                )

                plugin_text = (
                    f"；{installed_name} 已安装"
                )

                if status.get(
                    "coyote_plugin_legacy_name",
                    False,
                ):
                    plugin_text += (
                        "（旧文件名，建议点击安装/更新迁移为 Coyote.dll）"
                    )
            else:
                plugin_text = (
                    f"；{B.COYOTE_PLUGIN_FILENAME} 未安装"
                )

            plugin_path_text = status.get(
                "coyote_plugin_path",
                "",
            )

            self.bepinex_status_label.setText(
                (
                    "● BepInEx 已安装且结构完整"
                    + plugin_text
                    + "\n游戏："
                    + status.get(
                        "game_dir",
                        ""
                    )
                    + (
                        "\n插件："
                        + plugin_path_text
                        if plugin_path_text
                        else ""
                    )
                )
            )

            self.bepinex_status_label.setStyleSheet(
                (
                    "color:#46C58A;font-weight:700"
                    if status.get(
                        "coyote_plugin_installed",
                        False,
                    )
                    else "color:#F5B84B;font-weight:700"
                )
            )

        elif status.get(
            "installed",
            False,
        ):
            checks = status.get(
                "checks",
                {}
            )

            missing = [
                key
                for key, ok
                in checks.items()
                if not ok
            ]

            self.bepinex_status_label.setText(
                (
                    "◐ 检测到 BepInEx，但结构不完整"
                    "\n缺失："
                    + ", ".join(
                        missing
                    )
                )
            )

            self.bepinex_status_label.setStyleSheet(
                "color:#F5B84B;font-weight:700"
            )

        else:
            self.bepinex_status_label.setText(
                (
                    "○ 尚未安装 BepInEx\n"
                    + status.get(
                        "game_dir",
                        ""
                    )
                )
            )

            self.bepinex_status_label.setStyleSheet(
                "color:#A7B3C6;font-weight:700"
            )

    def set_bepinex_busy(
        self,
        busy,
    ):
        buttons = [
            getattr(
                self,
                "bepinex_check_btn",
                None,
            ),
            getattr(
                self,
                "bepinex_install_btn",
                None,
            ),
            getattr(
                self,
                "bepinex_local_btn",
                None,
            ),
            getattr(
                self,
                "coyote_install_btn",
                None,
            ),
        ]

        for button in buttons:
            if button is not None:
                button.setEnabled(
                    not busy
                )

        if hasattr(
            self,
            "bepinex_progress_bar",
        ):
            self.bepinex_progress_bar.setVisible(
                busy
            )

    def on_bepinex_progress(
        self,
        text,
    ):
        if hasattr(
            self,
            "bepinex_status_label",
        ):
            self.bepinex_status_label.setText(
                str(text)
            )

        self.feedback(
            str(text),
            2500,
        )

    def run_bepinex_install_worker(
        self,
        game_dir,
        zip_path=None,
    ):
        def worker():
            ok, message, result = (
                B.install_bepinex_peak(
                    game_dir=game_dir,
                    zip_path=zip_path,
                    progress_callback=(
                        lambda text:
                        self.bepinex_progress.emit(
                            str(text)
                        )
                    ),
                )
            )

            self.bepinex_finished.emit(
                bool(ok),
                str(message),
                result,
            )

        self.set_bepinex_busy(
            True
        )

        threading.Thread(
            target=worker,
            name="BepInExInstaller",
            daemon=True,
        ).start()

    def install_bepinex_online(self):
        game_dir = (
            self.current_peak_dir_for_install()
        )

        ok, message, validated = (
            B.validate_peak_game_dir(
                game_dir
            )
        )

        if not ok:
            self.msg_warning(
                "无法安装 BepInEx",
                "请先选择正确的 PEAK 游戏目录。",
                message,
            )
            return

        status = (
            B.get_bepinex_status(
                validated
            )
        )

        if status.get(
            "complete",
            False,
        ):
            title = "修复 / 重装 BepInEx"
            text = (
                "当前已经检测到完整 BepInEx。"
                "确定重新安装 PEAK 专用包吗？"
            )
            detail = (
                "程序只覆盖包中对应文件，并会先备份旧文件；"
                "现有 BepInEx/plugins 中的其他 Mod 不会被删除。"
            )
            confirm_text = "修复 / 重装"
        else:
            title = "安装 BepInEx"
            text = (
                "确定下载并安装 PEAK 专用 BepInExPack 吗？"
            )
            detail = (
                f"来源：Thunderstore\n"
                f"包版本：{B.BEPINEX_PEAK_PACKAGE_VERSION}\n"
                f"目标：{validated}\n"
                "安装前必须完全退出 PEAK。"
            )
            confirm_text = "下载并安装"

        if not self.ask_confirm(
            title,
            text,
            detail,
            confirm_text=confirm_text,
            cancel_text="取消",
        ):
            return

        self.run_bepinex_install_worker(
            validated,
            None,
        )

    def install_bepinex_local(self):
        game_dir = (
            self.current_peak_dir_for_install()
        )

        ok, message, validated = (
            B.validate_peak_game_dir(
                game_dir
            )
        )

        if not ok:
            self.msg_warning(
                "无法安装 BepInEx",
                "请先选择正确的 PEAK 游戏目录。",
                message,
            )
            return

        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 BepInEx ZIP",
            str(B.ROOT),
            "ZIP (*.zip)",
        )

        if not path:
            self.feedback(
                "已取消本地 BepInEx 安装。",
                2500,
            )
            return

        if not self.ask_confirm(
            "从本地 ZIP 安装 BepInEx",
            "确定把这个 ZIP 安装到 PEAK 吗？",
            (
                f"ZIP：{path}\n"
                f"目标：{validated}\n"
                "ZIP 必须包含 BepInEx 文件夹和 winhttp.dll。"
            ),
            confirm_text="安装",
            cancel_text="取消",
        ):
            return

        self.run_bepinex_install_worker(
            validated,
            path,
        )

    def on_bepinex_finished(
        self,
        ok,
        message,
        result,
    ):
        self.set_bepinex_busy(
            False
        )

        self.refresh_bepinex_status()

        if ok:
            detail = ""

            if isinstance(
                result,
                dict,
            ):
                copied = result.get(
                    "copied",
                    0,
                )

                backed_up = result.get(
                    "backed_up",
                    0,
                )

                backup_root = result.get(
                    "backup_root",
                    "",
                )

                archive = result.get(
                    "archive",
                    "",
                )

                detail = (
                    f"复制文件：{copied}\n"
                    f"备份旧文件：{backed_up}\n"
                    f"安装包缓存：{archive}"
                )

                if backup_root:
                    detail += (
                        f"\n备份目录：{backup_root}"
                    )

                detail += (
                    "\n\n首次安装后请启动一次 PEAK，"
                    "让 BepInEx 生成配置和日志。"
                )

            self.msg_info(
                "BepInEx 安装完成",
                message,
                detail,
            )

        else:
            self.msg_error(
                "BepInEx 安装失败",
                message,
                (
                    "如果 PEAK 正在运行，请完全退出游戏后重试。"
                ),
            )

    def install_coyote_dll_to_peak(
        self,
    ):
        game_dir = (
            self.current_peak_dir_for_install()
        )

        dll = (
            B.find_built_coyote_dll()
        )

        if dll is None:
            self.msg_warning(
                "没有找到 Coyote.dll",
                "软件中没有可用于安装的插件副本。",
                (
                    "打包版应包含：\n"
                    "plugin\\Coyote.dll\n\n"
                    "如果你正在运行源码版，可先执行：\n"
                    "dotnet build -c Release"
                ),
            )
            return

        if not self.ask_confirm(
            "安装 Coyote.dll",
            "确定把软件附带的 Coyote.dll 安装/更新到 PEAK 吗？",
            (
                f"源文件：{dll}\n"
                f"目标 PEAK：{game_dir or '未检测到'}\n"
                f"目标文件：BepInEx\\plugins\\{B.COYOTE_PLUGIN_FILENAME}"
            ),
            confirm_text="安装 / 更新",
            cancel_text="取消",
        ):
            return

        ok, message = (
            B.install_coyote_plugin(
                game_dir=game_dir,
                dll_path=dll,
            )
        )

        self.refresh_bepinex_status()

        if ok:
            self.msg_info(
                "Coyote.dll 安装成功",
                "插件已经复制到 PEAK 的 BepInEx/plugins。",
                message,
            )
        else:
            self.msg_error(
                "Coyote.dll 安装失败",
                "无法安装插件。",
                message,
            )

    def open_peak_plugins_folder(
        self,
    ):
        game_dir = (
            self.current_peak_dir_for_install()
        )

        ok, message, validated = (
            B.validate_peak_game_dir(
                game_dir
            )
        )

        if not ok:
            self.msg_warning(
                "无法打开目录",
                "没有有效的 PEAK 游戏目录。",
                message,
            )
            return

        target = (
            validated
            / "BepInEx"
            / "plugins"
        )

        try:
            target.mkdir(
                parents=True,
                exist_ok=True,
            )

            if sys.platform.startswith(
                "win"
            ):
                import os
                os.startfile(
                    str(target)
                )
            else:
                self.feedback(
                    str(target),
                    5000,
                )

        except Exception as e:
            self.msg_error(
                "打开目录失败",
                "无法打开 BepInEx/plugins。",
                str(e),
            )


    def load_appearance_controls(self):
        """
        把 appearance 字典同步回 Qt 控件。
        用于参数 JSON 导入后刷新界面。
        """
        self.bgen.blockSignals(
            True
        )
        self.bgop.blockSignals(
            True
        )
        self.bgblur.blockSignals(
            True
        )
        self.bgbright.blockSignals(
            True
        )
        self.bgfit.blockSignals(
            True
        )
        self.glassop.blockSignals(
            True
        )
        self.radius.blockSignals(
            True
        )
        self.borderop.blockSignals(
            True
        )
        self.showvals.blockSignals(
            True
        )
        self.showbar.blockSignals(
            True
        )
        self.compact.blockSignals(
            True
        )
        self.dec.blockSignals(
            True
        )

        try:
            self.bgen.setChecked(
                bool(
                    appearance[
                        "background_enabled"
                    ]
                )
            )

            self.bgop.setValue(
                int(
                    float(
                        appearance[
                            "background_opacity"
                        ]
                    )
                    * 100
                )
            )

            self.bgblur.setValue(
                int(
                    appearance[
                        "background_blur"
                    ]
                )
            )

            self.bgbright.setValue(
                int(
                    float(
                        appearance[
                            "background_brightness"
                        ]
                    )
                    * 100
                )
            )

            self.bgfit.setCurrentText(
                str(
                    appearance[
                        "background_fit"
                    ]
                )
            )

            self.glassop.setValue(
                int(
                    float(
                        appearance[
                            "glass_opacity"
                        ]
                    )
                    * 100
                )
            )

            self.radius.setValue(
                int(
                    appearance[
                        "glass_radius"
                    ]
                )
            )

            self.borderop.setValue(
                int(
                    float(
                        appearance[
                            "glass_border_opacity"
                        ]
                    )
                    * 100
                )
            )

            self.showvals.setChecked(
                bool(
                    appearance[
                        "show_values"
                    ]
                )
            )

            self.showbar.setChecked(
                bool(
                    appearance[
                        "show_progress"
                    ]
                )
            )

            self.compact.setChecked(
                bool(
                    appearance[
                        "compact_status_cards"
                    ]
                )
            )

            self.dec.setValue(
                int(
                    appearance[
                        "status_decimals"
                    ]
                )
            )

        finally:
            for widget in (
                self.bgen,
                self.bgop,
                self.bgblur,
                self.bgbright,
                self.bgfit,
                self.glassop,
                self.radius,
                self.borderop,
                self.showvals,
                self.showbar,
                self.compact,
                self.dec,
            ):
                widget.blockSignals(
                    False
                )

    def _read_appearance_controls(self):
        appearance.update({
            "background_enabled": self.bgen.isChecked(),
            "background_opacity": self.bgop.value()/100,
            "background_blur": self.bgblur.value(),
            "background_brightness": self.bgbright.value()/100,
            "background_fit": self.bgfit.currentText(),
            "glass_opacity": self.glassop.value()/100,
            "glass_radius": self.radius.value(),
            "glass_border_opacity": self.borderop.value()/100,
            "show_values": self.showvals.isChecked(),
            "show_progress": self.showbar.isChecked(),
            "compact_status_cards": self.compact.isChecked(),
            "status_decimals": self.dec.value(),
        })

    def _appearance_drag_started(
        self,
    ):
        self._appearance_dragging = True

    def _appearance_drag_finished(
        self,
    ):
        self._appearance_dragging = False

        # Let pending mouse paint finish before doing expensive work.
        QApplication.processEvents()

    def background_fast_changed(
        self,
        *args,
    ):
        self._read_appearance_controls()

        # Enable/disable and fit changes are infrequent. Build the final frame
        # asynchronously rather than repainting/scaling the existing wallpaper.
        self.bg.invalidate()

    def background_heavy_changed(
        self,
        *args,
    ):
        self._read_appearance_controls()
        self._appearance_drag_finished()

        # Background opacity / blur / brightness are all baked by the worker.
        self.schedule_background_refresh()

    def theme_style_changed(
        self,
        *args,
    ):
        self._read_appearance_controls()
        self._appearance_drag_finished()

        # One full stylesheet application after release instead of 5-10 times
        # per second while the handle is moving.
        self.apply_theme(
            refresh_background=False
        )

    def status_display_changed(
        self,
        *args,
    ):
        self._read_appearance_controls()

        for card in (
            self.status_cards.values()
        ):
            card.set_value(
                card._last
            )

    def appearance_changed(
        self,
        *args,
    ):
        # Used by "Save Appearance": apply one final complete state.
        self._read_appearance_controls()
        self._appearance_dragging = False

        self.apply_theme(
            refresh_background=False
        )

        self.schedule_background_refresh()

    def choose_background(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择背景图片",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp)",
        )
        if not path:
            self.feedback("已取消选择背景图片。",2500)
            return

        self.feedback("正在后台处理背景图片，界面可以继续操作……",5000)

        def worker():
            try:
                ASSET_DIR.mkdir(parents=True,exist_ok=True)
                with Image.open(path) as opened:
                    converted=opened.convert("RGB")
                    max_side=max(converted.size)
                    if max_side>2560:
                        scale=2560.0/max_side
                        converted=converted.resize(
                            (
                                max(1,int(converted.width*scale)),
                                max(1,int(converted.height*scale)),
                            ),
                            Image.Resampling.LANCZOS,
                        )
                    cache_path=ASSET_DIR/"wallpaper.jpg"
                    converted.save(cache_path,"JPEG",quality=92,optimize=False)
                self.background_import_finished.emit(True,str(cache_path),str(path))
            except Exception as e:
                self.background_import_finished.emit(False,f"{type(e).__name__}: {e}",str(path))

        threading.Thread(target=worker,name="CoyoteBackgroundImport",daemon=True).start()

    def on_background_import_finished(self,ok,result,original):
        if not ok:
            self.msg_error("背景图片加载失败","无法处理这张背景图片。",result)
            return

        cache_path=Path(result)
        appearance["background_image"]=rel_root(cache_path)
        appearance["background_enabled"]=True
        self.bgen.blockSignals(True)
        self.bgen.setChecked(True)
        self.bgen.blockSignals(False)
        self.bgpath.setText(appearance["background_image"])
        self.bg.invalidate()

        ok_save,save_message=save_full_config()
        detail=(
            f"原图：{original}\n"
            f"优化缓存：{cache_path}\n"
            "背景处理已在后台完成。"
        )
        if ok_save:
            self.msg_info("背景设置成功","背景图片已经应用并保存。",detail)
        else:
            self.msg_warning("背景已应用，但配置保存失败","当前背景已经生效。",detail+"\n"+save_message)

    def clear_background(self):
        if not self.ask_confirm(
            "恢复纯色背景",
            "确定关闭当前背景图片吗？",
            "保存到 assets/background/wallpaper.jpg 的图片文件不会删除，之后仍可重新启用或选择其他图片。",
            confirm_text="恢复纯色",
            cancel_text="取消",
        ):
            return

        appearance[
            "background_image"
        ] = ""

        appearance[
            "background_enabled"
        ] = False

        self.bgen.setChecked(
            False
        )

        self.bgpath.setText(
            "未选择背景"
        )

        self.bg.invalidate()

        ok_save, save_message = (
            save_full_config()
        )

        if ok_save:
            self.msg_info(
                "背景已关闭",
                "已经恢复为纯色背景。",
                "保存的 wallpaper.jpg 文件仍保留在项目 assets/background 目录。",
            )
        else:
            self.msg_error(
                "背景配置保存失败",
                "当前窗口已经恢复纯色，但配置文件保存失败。",
                save_message,
            )
    def choose_accent(self):
        c=QColorDialog.getColor(QColor(appearance.get("accent","#5B8CFF")),self,"选择强调色")
        if c.isValid():self.set_accent(c.name())
    def set_accent(self,c):appearance["accent"]=c;self.apply_theme()
    def save_appearance(self):
        self.appearance_changed()

        ok, msg = save_full_config()

        if ok:
            self.msg_info(
                "外观保存成功",
                "外观设置已经保存。",
                "下次启动会自动恢复背景、透明度、模糊、亮度、毛玻璃和数据显示选项。",
            )
        else:
            self.msg_error(
                "外观保存失败",
                "无法写入外观配置。",
                msg,
            )

    def toggle_master(self, state):
        B.master_output_enabled = (
            state
            == Qt.CheckState.Checked.value
        )

        state_text = (
            "已开启"
            if B.master_output_enabled
            else "已关闭"
        )

        B.add_log(
            "系统",
            "总输出开关",
            state_text,
        )

        self.feedback(
            f"电击总输出{state_text}。",
            3000,
        )

        if not B.master_output_enabled:
            B.clear_device_output(
                "总输出开关关闭"
            )

    def stop_output(self):
        # 紧急停止不弹确认框，避免阻塞停止动作。
        self.master.setChecked(
            False
        )

        ok, message = B.clear_device_output(
            "用户点击立即停止"
        )

        if ok:
            self.feedback(
                "已发送停止全部输出指令。",
                5000,
            )
        else:
            self.feedback(
                f"停止输出：{message}",
                5000,
            )
    @staticmethod
    def pct(v):
        try:return float(v)*100
        except:return 0
    @staticmethod
    def yn(v):
        return I18N.tr(
            "是"
            if bool(v)
            else "否"
        )

    def refresh_logs(self, force=False):
        with B.log_lock:
            logs = list(B.event_logs)
            revision = int(getattr(B, "log_revision", len(logs)))

        query = ""
        if hasattr(self, "log_search"):
            query = self.log_search.text().strip().lower()

        if not force and revision == getattr(self, "last_log_revision", -1):
            return

        self.last_log_revision = revision
        self.last_log_count = len(logs)

        localized_all = [I18N.localize_log_record(item) for item in logs]
        if query:
            localized = [
                item for item in localized_all
                if query in " ".join((
                    str(item.get("time", "")),
                    str(item.get("category", "")),
                    str(item.get("event", "")),
                    str(item.get("detail", "")),
                )).lower()
            ]
        else:
            localized = localized_all

        if hasattr(self, "log_search_count"):
            self.log_search_count.setText(
                f"{len(localized)} / {len(localized_all)} 条" if query else f"{len(localized_all)} 条"
            )

        self.logtable.setRowCount(len(localized))
        for row, item in enumerate(localized):
            values = (item["time"], item["category"], item["event"], item["detail"])
            for column, value in enumerate(values):
                self.logtable.setItem(row, column, QTableWidgetItem(str(value)))

        # 总览最近事件始终显示完整日志的最后 12 条，不受搜索框过滤。
        recent = localized_all[-12:]
        self.recent.setRowCount(len(recent))
        for row, item in enumerate(recent):
            values = (item["time"], item["category"], item["event"], item["detail"])
            for column, value in enumerate(values):
                self.recent.setItem(row, column, QTableWidgetItem(str(value)))

        if localized:
            self.logtable.scrollToBottom()
        if recent:
            self.recent.scrollToBottom()

    def refresh_ui(self):
        # Slider thumb responsiveness has priority while tuning appearance.
        # Game rule evaluation continues independently in backend.py.
        if getattr(
            self,
            "_appearance_dragging",
            False,
        ):
            return

        with B.dg_lock:
            d = dict(
                B.dg
            )

        with B.peak_lock:
            p = (
                dict(
                    B.latest_peak
                )
                if B.latest_peak
                else None
            )

        peak_runtime = (
            B.get_peak_runtime_state()
        )

        peak_state = peak_runtime[
            "state"
        ]

        if peak_state == "in_game":
            peak_base = I18N.tr(
                "局内 / 遥测中"
            )
            peak_text = (
                "● "
                + peak_base
            )
            peak_color = "#46C58A"

        elif (
            peak_state
            == "lobby_or_loading"
        ):
            raw_scene = (
                (p or {}).get(
                    "scene"
                )
                or "大厅 / 加载中"
            )

            scene = I18N.tr_dynamic(
                raw_scene
            )

            peak_text = (
                "◐ "
                + scene
            )
            peak_color = "#F5B84B"

        else:
            peak_base = I18N.tr(
                "PEAK 未启动"
            )
            peak_text = (
                "○ "
                + peak_base
            )
            peak_color = "#8FA0B8"

        self.conn[
            "peak"
        ].setText(
            peak_text
        )

        self.conn[
            "peak"
        ].setStyleSheet(
            (
                f"color:{peak_color};"
                "font-weight:700;"
            )
        )

        self.side_peak.setText(
            (
                "PEAK · "
                + peak_text
                .replace(
                    "● ",
                    "",
                )
                .replace(
                    "◐ ",
                    "",
                )
                .replace(
                    "○ ",
                    "",
                )
            )
        )

        age = peak_runtime.get(
            "last_packet_age"
        )

        if age is None:
            packet_text = I18N.tr(
                "尚未收到"
            )

        elif age < 60:
            packet_text = (
                f"{age:.1f} "
                + I18N.tr(
                    "秒前"
                )
                if age < 1
                else (
                    f"{age:.0f} "
                    + I18N.tr(
                        "秒前"
                    )
                )
            )

        else:
            packet_text = (
                f"{age/60:.1f} "
                + I18N.tr(
                    "分钟前"
                )
            )

        self.conn[
            "peak_packet"
        ].setText(
            packet_text
        )

        scene = str(
            (p or {}).get(
                "scene"
            )
            or "-"
        )

        self.conn[
            "scene"
        ].setText(
            I18N.tr_dynamic(
                scene
            )
        )

        dg_connected = bool(
            d.get(
                "app_id"
            )
        )

        self.conn[
            "dg"
        ].setText(
            (
                "● "
                + I18N.tr(
                    "已接入"
                )
                if dg_connected
                else (
                    "○ "
                    + I18N.tr(
                        "未接入"
                    )
                )
            )
        )

        self.side_dg.setText(
            (
                "DG APP · "
                + I18N.tr(
                    "已接入"
                    if dg_connected
                    else "未接入"
                )
            )
        )

        dev = " ".join(
            x
            for x in (
                d.get(
                    "device_name"
                ),
                d.get(
                    "device_type"
                ),
            )
            if x
        )

        self.conn[
            "device"
        ].setText(
            dev or "-"
        )

        self.conn[
            "slot"
        ].setText(
            str(
                d.get(
                    "slot_id"
                )
                or "-"
            )
        )

        self.conn[
            "ip"
        ].setText(
            B.LAN_IP
        )

        self.conn[
            "ports"
        ].setText(
            (
                f"PEAK {B.PEAK_PORT} / "
                f"DG {B.DG_PORT}"
            )
        )

        if d.get(
            "has_device"
        ) is True:
            ds = I18N.tr(
                "已检测到设备"
            )

        elif d.get(
            "has_device"
        ) is False:
            ds = I18N.tr(
                "槽位无设备"
            )

        else:
            ds = "-"

        self.conn[
            "device_state"
        ].setText(
            ds
        )

        if hasattr(
            self,
            "manual_device_label",
        ):
            self.manual_device_label.setText(
                dev
                or I18N.tr(
                    "等待设备接入"
                )
            )

            self.manual_slot_label.setText(
                (
                    I18N.tr(
                        "slotId"
                    )
                    + ": "
                    + str(
                        d.get(
                            "slot_id"
                        )
                        or "-"
                    )
                )
            )

        self.header_connection.setText(
            (
                peak_text
                + "   ·   DG "
                + I18N.tr(
                    "已接入"
                    if dg_connected
                    else "未接入"
                )
            )
        )

        if (
            p
            and p.get(
                "hasCharacter",
                True,
            )
        ):
            dec = int(
                appearance[
                    "status_decimals"
                ]
            )

            held = (
                p.get(
                    "heldItem"
                )
                if isinstance(
                    p.get(
                        "heldItem"
                    ),
                    dict,
                )
                else {}
            )

            vals = {
                "hp":
                    f'{float(p.get("hp",0)):.{dec}f}%',

                "injury":
                    f'{self.pct(p.get("injury",0)):.{dec}f}%',

                "stamina":
                    f'{self.pct(p.get("staminaCurrent",0)):.{dec}f}%',

                "stamina_max":
                    f'{self.pct(p.get("staminaMax",0)):.{dec}f}%',

                "extra":
                    f'{self.pct(p.get("staminaExtra",0)):.{dec}f}%',

                "dead":
                    self.yn(
                        p.get(
                            "dead"
                        )
                    ),

                "passed":
                    self.yn(
                        p.get(
                            "passedOut"
                        )
                    ),

                "climbing":
                    self.yn(
                        p.get(
                            "climbing"
                        )
                    ),

                "grounded":
                    self.yn(
                        p.get(
                            "grounded"
                        )
                    ),

                "crouching":
                    self.yn(
                        p.get(
                            "crouching"
                        )
                    ),

                "held":
                    (
                        held.get(
                            "name"
                        )
                        or I18N.tr(
                            "空手"
                        )
                    ),

                "speed":
                    str(
                        p.get(
                            "speed",
                            "-",
                        )
                    ),
            }

            for key, value in (
                vals.items()
            ):
                self.core[
                    key
                ].setText(
                    str(value)
                )

            statuses = p.get(
                "statuses",
                [],
            )

            names = p.get(
                "statusNames",
                [],
            )

            index_by_name = (
                {
                    str(name): index
                    for index, name
                    in enumerate(
                        names
                    )
                }
                if isinstance(
                    names,
                    list,
                )
                else {}
            )

            for index, (
                key,
                _,
            ) in enumerate(
                B.STATUS_ORDER
            ):
                runtime_index = (
                    index_by_name.get(
                        key,
                        index,
                    )
                )

                self.status_cards[
                    key
                ].set_value(
                    self.pct(
                        statuses[
                            runtime_index
                        ]
                    )
                    if runtime_index
                    < len(statuses)
                    else 0
                )

        elif p:
            for key in self.core:
                self.core[
                    key
                ].setText(
                    "-"
                )

        if p:
            self.refresh_telemetry(
                p
            )

        with B.log_lock:
            count = B.output_count

            output = (
                dict(
                    B.last_output
                )
                if B.last_output
                else None
            )

        self.ocount.setText(
            str(count)
        )

        if output:
            event_name = I18N.tr_dynamic(
                output[
                    "event"
                ]
            )

            change = I18N.tr_dynamic(
                output[
                    "change"
                ]
            )

            self.oevent.setText(
                (
                    f"{event_name}："
                    f"{change}"
                )
            )

            base_word = I18N.tr(
                "基础"
            )

            level_word = I18N.tr(
                "等级"
            )

            self.oa.setText(
                (
                    f'{base_word} '
                    f'{output.get("a_base_intensity",output["a_intensity"])} '
                    f'+ {output.get("a_bonus",0)} '
                    f'→ {level_word} '
                    f'{output["a_intensity"]} / '
                    f'{output["a_duration"]} ms / '
                    f'{I18N.tr_dynamic(output["a_waveform"])}'
                )
            )

            self.ob.setText(
                (
                    f'{base_word} '
                    f'{output.get("b_base_intensity",output["b_intensity"])} '
                    f'+ {output.get("b_bonus",0)} '
                    f'→ {level_word} '
                    f'{output["b_intensity"]} / '
                    f'{output["b_duration"]} ms / '
                    f'{I18N.tr_dynamic(output["b_waveform"])}'
                )
            )

        for key, value in (
            (
                "server",
                d.get(
                    "server"
                ),
            ),
            (
                "controller",
                d.get(
                    "controller_id"
                )
                or "-",
            ),
            (
                "app",
                d.get(
                    "app_id"
                )
                or "-",
            ),
            (
                "device",
                dev or "-",
            ),
            (
                "slot",
                d.get(
                    "slot_id"
                )
                or "-",
            ),
            (
                "error",
                d.get(
                    "error"
                )
                or "-",
            ),
        ):
            self.detail[
                key
            ].setText(
                I18N.tr_dynamic(
                    str(value)
                )
            )

        pairing = B.pairing_url(
            d.get(
                "controller_id"
            )
        )

        self.url.setText(
            pairing or ""
        )

        self.update_qr(
            pairing
        )

        self.refresh_logs()

    def closeEvent(self,e):
        B.master_output_enabled=False
        try:B.clear_device_output("程序关闭")
        except:pass
        B.stop_event.set()
        try:
            if B.udp_socket:B.udp_socket.close()
        except:pass
        try:
            if B.dg_ws:B.dg_ws.close()
        except:pass
        try:
            if B.dg_process and B.dg_process.poll() is None:B.dg_process.terminate()
        except:pass
        save_full_config();e.accept()


def main():
    load_full_config()
    B.ensure_custom_rule_assets()
    B.load_custom_rules()
    B.add_log(
        "系统",
        "Coyote Qt Controller 启动",
        f"本机 IP={B.LAN_IP}",
    )
    B.start_server()
    threading.Thread(
        target=B.websocket_loop,
        name="DGLAB-WebSocket",
        daemon=True,
    ).start()
    threading.Thread(
        target=B.peak_udp_loop,
        name="PEAK-UDP",
        daemon=True,
    ).start()
    app=QApplication(sys.argv)
    app.setStyle("Fusion")
    w=Window()
    w.show()
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())