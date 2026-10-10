"""Preview a sample: a waveform with its loop, and Play/Stop. Owns the audio player."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from akaiutil_gui import describe, settings
from akaiutil_gui.audio import preview
from akaiutil_gui.audio.preview import PreviewPlayer
from akaiutil_gui.audio.sample import LoadedSample, PlaybackPlan, playback_plan
from akaiutil_gui.formatting import human_duration, note_name

from .waveform_preview import PreviewWaveform

PLAY_TEXT, STOP_TEXT = "▶  Play", "■  Stop"


class PreviewPanel(QWidget):
    def __init__(self, parent=None, player: PreviewPlayer | None = None):
        super().__init__(parent)
        self.player = player or PreviewPlayer(self)
        self._sample: LoadedSample | None = None

        self.wave = PreviewWaveform()
        self.play_button = QPushButton(PLAY_TEXT)
        self.play_button.setToolTip("Play or stop (Space)")
        self.play_button.setMinimumWidth(96)
        self.loop_check = QCheckBox("Loop Preview")
        self.loop_check.setToolTip("Play the sample's loop (the attack once, then the loop repeating)")
        self.loop_combo = QComboBox()
        self.loop_combo.setToolTip("Which of the sample's loops to play")
        self.loop_combo.hide()
        self.info_label = QLabel()
        self.info_label.setObjectName("mutedLabel")
        self.info_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        row = QHBoxLayout()
        row.setSpacing(10)
        for w in (self.play_button, self.loop_check, self.loop_combo):
            row.addWidget(w)
        row.addWidget(self.info_label, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.wave)
        layout.addLayout(row)

        self.play_button.clicked.connect(self.toggle)
        self.loop_check.toggled.connect(self._options_changed)
        self.loop_combo.currentIndexChanged.connect(self._options_changed)
        self.wave.seekRequested.connect(self.play_from)
        self.player.position_changed.connect(self.wave.set_playhead)
        self.player.finished.connect(self._on_finished)
        self.clear()

    # ----------------------------------------------------------------- state
    @property
    def sample(self) -> LoadedSample | None:
        return self._sample

    def is_playing(self) -> bool:
        return self.player.is_playing()

    def clear(self) -> None:
        self.player.stop()
        self._sample = None
        self.wave.clear()
        self.play_button.setEnabled(False)
        self.loop_check.setEnabled(False)
        self.loop_combo.hide()
        self.info_label.setText("")

    def show_loading(self, name: str) -> None:
        self.clear()
        self.info_label.setText(f"Loading {name}…")

    def show_error(self, message: str) -> None:
        self.clear()
        self.info_label.setText(f"Couldn't load the sample: {message}")

    def set_sample(self, sample: LoadedSample) -> None:
        self.player.stop()
        self._sample = sample
        info = sample.info
        loops = info is not None and info.has_loop_to_preview
        self.loop_check.blockSignals(True)
        self.loop_check.setChecked(settings.get_preview_loops())  # the Settings default, for each new sample
        self.loop_check.blockSignals(False)
        self.loop_check.setEnabled(loops)
        self.loop_check.setToolTip(
            "Play the sample's loop (the attack once, then the loop repeating)" if loops else "This sample doesn't loop"
        )
        self.loop_combo.blockSignals(True)
        self.loop_combo.clear()
        if loops:
            for loop in info.loops:
                self.loop_combo.addItem(f"Loop {loop.slot} · {'Hold' if loop.time_ms is None else f'{loop.time_ms} ms'}", loop.slot)
        self.loop_combo.blockSignals(False)
        self.loop_combo.setVisible(loops and len(info.loops) > 1)
        self.play_button.setEnabled(not preview.audio_disabled())
        self.play_button.setToolTip("Play or stop (Space)" if self.play_button.isEnabled() else "Audio is turned off")
        self._refresh()

    def _plan(self) -> PlaybackPlan:
        return playback_plan(self._sample, self.loop_combo.currentData(), self.loop_check.isChecked() and self.loop_check.isEnabled())

    def _refresh(self) -> None:
        """Show the sample as it will play: its play range, the loop that will repeat, and a one-line summary."""
        sample = self._sample
        plan = self._plan()
        self.wave.set_audio(sample.samples, plan.start, plan.end)
        self.wave.set_loop(plan.loop)
        info = sample.info
        parts = [f"{sample.rate / 1000:g} kHz", human_duration(sample.frames / sample.rate)]
        if info is not None:
            if info.root_key is not None:
                parts.append(note_name(info.root_key))
            parts.append(describe.play_mode_label(info))
        if plan.loop is not None:
            parts.append(f"loop {plan.loop.slot}: frames {plan.loop_start:,}–{plan.loop_end:,}, {'hold' if plan.dwell_ms is None else f'{plan.dwell_ms} ms'}")
        self.info_label.setText("  ·  ".join(parts))

    def _options_changed(self) -> None:
        if self._sample is None:
            return
        self._refresh()
        if self.player.is_playing():  # change what's playing right away, so the choice can be heard
            self.play()

    # --------------------------------------------------------------- playback
    def play(self) -> None:
        if self._sample is None or not self.play_button.isEnabled():
            return
        sample, plan = self._sample, self._plan()
        if plan.looping:
            self.player.play_loop(
                sample.samples, plan.start, plan.loop_start, plan.loop_end, plan.end, sample.rate,
                dwell_ms=plan.dwell_ms, loop_tune_cents=plan.loop_tune_cents,
            )
        else:
            self.player.play(sample.samples, plan.start, plan.end, sample.rate)
        if self.player.is_playing():
            self.play_button.setText(STOP_TEXT)
        else:
            self.info_label.setText("Couldn't open the audio output (check Settings > Audio Preview).")

    def play_from(self, frame: int) -> None:
        """Click on the waveform: play once from there to the end marker (or the end of the audio)."""
        if self._sample is None or not self.play_button.isEnabled():
            return
        plan = self._plan()
        self.player.play(self._sample.samples, frame, max(frame, plan.end), self._sample.rate)
        if self.player.is_playing():
            self.play_button.setText(STOP_TEXT)

    def stop(self) -> None:
        self.player.stop()

    def toggle(self) -> None:
        if self.player.is_playing():
            self.player.stop()
        else:
            self.play()

    def _on_finished(self) -> None:
        self.wave.set_playhead(None)
        self.play_button.setText(PLAY_TEXT)
