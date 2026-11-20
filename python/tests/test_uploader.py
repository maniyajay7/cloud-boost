"""
Tests for cloudboost.uploader AnimatedProgressBar
"""

import sys
import io
import pytest
from cloudboost.uploader import AnimatedProgressBar, BAR_FILLED, BAR_EMPTY


class TestAnimatedProgressBar:
    def test_init(self):
        bar = AnimatedProgressBar(total_size=1000, bar_length=20)
        assert bar.total_size == 1000
        assert bar.bar_length == 20
        assert bar.percent == 0.0
        assert bar.speed == "--"
        assert bar.eta == "--"

    def test_update_from_rclone_stats(self):
        bar = AnimatedProgressBar(total_size=200 * 1024 * 1024)
        sample_line = "Transferred:   32.450 MiB / 215.840 MiB, 15%, 890.120 KiB/s, ETA 3m25s"
        bar.update_from_rclone(sample_line)
        assert bar.percent == 15.0
        assert "KiB/s" in bar.speed
        assert bar.eta == "3m25s"
        assert "32.450 MiB" in bar.xfer_str

    def test_update_from_rclone_compact(self):
        bar = AnimatedProgressBar(total_size=100)
        sample_line = "* file.zst: 45% /100MiB, 1.2MiB/s, 10s"
        bar.update_from_rclone(sample_line)
        assert bar.percent == 45.0
        assert "MiB/s" in bar.speed
        assert bar.eta == "10s"

    def test_render_output(self, monkeypatch):
        bar = AnimatedProgressBar(total_size=1000, bar_length=10)
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        bar.percent = 50.0
        bar.speed = "10 MB/s"
        bar.eta = "5s"
        bar.render()

        out = captured.getvalue()
        assert "50.0%" in out
        assert "10 MB/s" in out
        assert "5s" in out

    def test_complete_output(self, monkeypatch):
        bar = AnimatedProgressBar(total_size=1024 * 1024, bar_length=10)
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        bar.complete()
        out = captured.getvalue()
        assert "100.0%" in out
        assert "Done!" in out

    def test_fail_output(self, monkeypatch):
        bar = AnimatedProgressBar(total_size=1000, bar_length=10)
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        bar.percent = 25.0
        bar.fail()
        out = captured.getvalue()
        assert "25.0%" in out
        assert "Interrupted" in out
