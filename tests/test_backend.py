"""Comprehensive test suite for Pulse CLI backend engine."""

import array
import math
import time
import unittest
from pathlib import Path

from pulse.audio import AudioEngine, AudioState
from pulse.dsp import AudioDSP, ReplayGain, StereoVUMeter, VisualizerMode, WaveformOscilloscope
from pulse.metadata import (
    DitherAlgorithm,
    LyricsLine,
    TrackLyrics,
    TrackMetadata,
    _dither_atkinson,
    _dither_bayer,
    _dither_floyd_steinberg,
    format_duration,
    format_file_size,
    parse_lrc,
)
from pulse.queue_manager import QueueManager, RepeatMode


def make_dummy_track(idx: int = 1) -> TrackMetadata:
    return TrackMetadata(
        path=Path(f"/tmp/test_track_{idx}.mp3"),
        title=f"Test Song {idx}",
        artist=f"Artist {idx}",
        album=f"Album {idx}",
        year="2024",
        genre="Electronic",
        duration=180.0,
        bitrate=320,
        sample_rate=44100,
        channels="Stereo",
        file_size_bytes=5 * 1024 * 1024,
        file_format="MP3",
    )


class TestFormatters(unittest.TestCase):
    def test_format_duration_normal(self):
        self.assertEqual(format_duration(0), "00:00")
        self.assertEqual(format_duration(45), "00:45")
        self.assertEqual(format_duration(75), "01:15")
        self.assertEqual(format_duration(3665), "01:01:05")

    def test_format_duration_edge_cases(self):
        self.assertEqual(format_duration(None), "00:00")
        self.assertEqual(format_duration(-10), "00:00")
        self.assertEqual(format_duration(float("nan")), "00:00")
        self.assertEqual(format_duration(float("inf")), "00:00")
        self.assertEqual(format_duration(float("-inf")), "00:00")

    def test_format_file_size(self):
        self.assertEqual(format_file_size(0), "0 B")
        self.assertEqual(format_file_size(500), "500 B")
        self.assertEqual(format_file_size(1024), "1.0 KB")
        self.assertEqual(format_file_size(1024 * 1024 * 5), "5.0 MB")
        self.assertEqual(format_file_size(1024 * 1024 * 1024 * 2), "2.00 GB")


class TestPixelArt(unittest.TestCase):
    def test_empty_pixel_art_bounds(self):
        for w in range(0, 30):
            for h in range(0, 15):
                lines = TrackMetadata.empty_pixel_art(w, h)
                self.assertLessEqual(len(lines), max(0, h))
                for line in lines:
                    self.assertLessEqual(len(line), max(0, w))

    def test_pixel_art_caching(self):
        track = make_dummy_track()
        art1 = track.get_pixel_art(14, 5)
        art2 = track.get_pixel_art(14, 5)
        self.assertEqual(art1, art2)
        self.assertEqual(len(art1), 5)


class TestQueueManager(unittest.TestCase):
    def setUp(self):
        self.qm = QueueManager()
        self.tracks = [make_dummy_track(i) for i in range(1, 6)]
        self.qm.add_tracks(self.tracks)

    def test_initial_state(self):
        self.assertEqual(self.qm.count, 5)
        self.assertIsNone(self.qm.current_track)
        self.assertEqual(self.qm.current_index, -1)

    def test_jump_to(self):
        t = self.qm.jump_to(2)
        self.assertIsNotNone(t)
        self.assertEqual(t.title, "Test Song 3")
        self.assertEqual(self.qm.current_index, 2)

    def test_sequential_navigation(self):
        self.qm.jump_to(0)
        self.assertEqual(self.qm.current_track.title, "Test Song 1")
        nxt = self.qm.next_track()
        self.assertEqual(nxt.title, "Test Song 2")
        prv = self.qm.prev_track()
        self.assertEqual(prv.title, "Test Song 1")

    def test_repeat_track_mode(self):
        self.qm.jump_to(1)
        self.qm.repeat_mode = RepeatMode.TRACK
        # Auto-advance should repeat the same track
        auto_nxt = self.qm.next_track(auto_advance=True)
        self.assertEqual(auto_nxt.title, "Test Song 2")
        # Manual next should skip forward to next track
        manual_nxt = self.qm.next_track(auto_advance=False)
        self.assertEqual(manual_nxt.title, "Test Song 3")

    def test_repeat_queue_mode(self):
        self.qm.jump_to(4)
        self.qm.repeat_mode = RepeatMode.QUEUE
        nxt = self.qm.next_track()
        self.assertEqual(nxt.title, "Test Song 1")
        prv = self.qm.prev_track()
        self.assertEqual(prv.title, "Test Song 5")

    def test_shuffle_cycle_and_prev(self):
        self.qm.jump_to(0)
        self.qm.repeat_mode = RepeatMode.OFF
        self.qm.toggle_shuffle()
        self.assertTrue(self.qm.is_shuffled)

        played = [self.qm.current_track.title]
        for _ in range(4):
            nxt = self.qm.next_track()
            if nxt:
                played.append(nxt.title)

        # Every track must be played exactly once
        self.assertEqual(len(played), 5)
        self.assertEqual(len(set(played)), 5)

        # Prev track should step back along the played order
        prev_t = self.qm.prev_track()
        self.assertEqual(prev_t.title, played[-2])

    def test_move_track(self):
        self.qm.jump_to(1)  # Test Song 2
        success = self.qm.move_track(1, 3)
        self.assertTrue(success)
        self.assertEqual(self.qm.current_index, 3)
        self.assertEqual(self.qm.tracks[3].title, "Test Song 2")

    def test_remove_at(self):
        self.qm.jump_to(2)  # Test Song 3
        removed = self.qm.remove_at(2)
        self.assertEqual(removed.title, "Test Song 3")
        self.assertEqual(self.qm.count, 4)
        # current_index should now point to the track that slid into position 2
        self.assertEqual(self.qm.current_track.title, "Test Song 4")

    def test_clear(self):
        self.qm.jump_to(1)
        self.qm.clear()
        self.assertEqual(self.qm.count, 0)
        self.assertEqual(self.qm.current_index, -1)
        self.assertIsNone(self.qm.current_track)


class TestAudioEngine(unittest.TestCase):
    def setUp(self):
        self.engine = AudioEngine()

    def tearDown(self):
        self.engine.close()

    def test_initial_state(self):
        self.assertEqual(self.engine.state, AudioState.STOPPED)
        self.assertEqual(self.engine.volume, 0.85)
        self.assertFalse(self.engine.is_muted)

    def test_volume_controls(self):
        self.engine.set_volume(0.5)
        self.assertAlmostEqual(self.engine.volume, 0.5)
        # Test clamping
        self.engine.set_volume(1.5)
        self.assertEqual(self.engine.volume, 1.0)
        self.engine.set_volume(-0.5)
        self.assertEqual(self.engine.volume, 0.0)

        # Test mute toggle
        self.engine.set_volume(0.7)
        self.engine.toggle_mute()
        self.assertTrue(self.engine.is_muted)
        self.engine.toggle_mute()
        self.assertFalse(self.engine.is_muted)
        self.assertAlmostEqual(self.engine.volume, 0.7)

    def test_seek_to_end_triggers_finished(self):
        self.engine._samples = [0] * (44100 * 2)
        self.engine._total_frames = 44100
        self.engine._current_frame = 0
        self.engine.state = AudioState.PLAYING

        # Seek past end
        self.engine.seek(10.0)
        self.assertEqual(self.engine._current_frame, 44100)
        self.assertTrue(self.engine.track_finished_flag)
        self.assertEqual(self.engine.state, AudioState.STOPPED)

    def test_unload(self):
        self.engine._samples = [1, 2, 3]
        self.engine._total_frames = 3
        self.engine._current_frame = 1
        self.engine.state = AudioState.PLAYING

        self.engine.unload()
        self.assertIsNone(self.engine._samples)
        self.assertEqual(self.engine._total_frames, 0)
        self.assertEqual(self.engine._current_frame, 0)
        self.assertEqual(self.engine.state, AudioState.STOPPED)

    def test_visualizer_mode_cycling(self):
        self.assertEqual(self.engine.get_visualizer_mode(), VisualizerMode.SPECTRUM)
        m2 = self.engine.cycle_visualizer_mode()
        self.assertEqual(m2, VisualizerMode.WAVEFORM)
        m3 = self.engine.cycle_visualizer_mode()
        self.assertEqual(m3, VisualizerMode.STEREO_VU)
        m4 = self.engine.cycle_visualizer_mode()
        self.assertEqual(m4, VisualizerMode.SPECTRUM)

    def test_replay_gain_toggle(self):
        self.assertTrue(self.engine.replay_gain_enabled)
        state = self.engine.toggle_replay_gain()
        self.assertFalse(state)
        self.assertFalse(self.engine.replay_gain_enabled)


class TestAudioDSP(unittest.TestCase):
    def setUp(self):
        self.dsp = AudioDSP()

    def test_fft_spectrum_silence(self):
        silence = [0] * 512
        levels = self.dsp.compute_spectrum(silence)
        self.assertEqual(len(levels), 64)
        for val in levels:
            self.assertEqual(val, 0.0)

    def test_fft_spectrum_sine_wave(self):
        # 441 Hz sine wave at 44100 Hz sample rate (period = 100 samples)
        # 512 samples with ~5.12 full periods
        chunk = [int(16000 * math.sin(2.0 * math.pi * 441.0 * i / 44100.0)) for i in range(512)]
        levels = self.dsp.compute_spectrum(chunk)
        self.assertEqual(len(levels), 64)
        self.assertTrue(all(0.0 <= lvl <= 1.0 for lvl in levels))
        # Fundamental ~441 Hz falls into band 4-6
        max_idx = levels.index(max(levels))
        self.assertIn(max_idx, range(3, 8))

    def test_stereo_vu_meter(self):
        chunk = array.array("h", [16000, -8000, 12000, -6000])
        peak_l, peak_r, db_l, db_r = StereoVUMeter.compute(chunk, channels=2)
        self.assertGreater(peak_l, 0.0)
        self.assertLessEqual(peak_l, 1.0)
        self.assertGreater(peak_r, 0.0)
        self.assertLessEqual(peak_r, 1.0)
        self.assertGreaterEqual(db_l, -60.0)
        self.assertGreaterEqual(db_r, -60.0)

    def test_waveform_oscilloscope(self):
        chunk = array.array("h", [int(16000 * math.sin(i / 10.0)) for i in range(512)])
        lines = WaveformOscilloscope.render(chunk, width=30, height=8, channels=1)
        self.assertEqual(len(lines), 6)  # inner height = height - 2
        for line in lines:
            self.assertEqual(len(line), 28)  # inner width = width - 2

    def test_replay_gain_math(self):
        self.assertAlmostEqual(ReplayGain.parse_gain_db("-6.0 dB"), -6.0)
        self.assertAlmostEqual(ReplayGain.parse_gain_db("+3.2 LUFS"), 3.2)
        self.assertAlmostEqual(ReplayGain.parse_gain_db("invalid"), 0.0)
        # 0 dB gain -> multiplier 1.0
        self.assertAlmostEqual(ReplayGain.to_linear_scale(0.0), 1.0)
        # -6 dB gain -> multiplier ~0.501
        self.assertAlmostEqual(ReplayGain.to_linear_scale(-6.0), 10 ** (-6.0 / 20.0), places=3)


class TestDitherAndLyrics(unittest.TestCase):
    def test_dither_enum_cycle(self):
        d = DitherAlgorithm.ATKINSON
        d = d.next_algorithm()
        self.assertEqual(d, DitherAlgorithm.FLOYD_STEINBERG)
        d = d.next_algorithm()
        self.assertEqual(d, DitherAlgorithm.BAYER)
        d = d.next_algorithm()
        self.assertEqual(d, DitherAlgorithm.ATKINSON)

    def test_lrc_parser_synced(self):
        lrc_text = """[ti:Test Title]
[ar:Test Artist]
[00:04.50]Line 1 lyrics
[00:12.80]Line 2 lyrics
[00:25.00]Line 3 lyrics
"""
        lyrics = parse_lrc(lrc_text)
        self.assertIsNotNone(lyrics)
        self.assertTrue(lyrics.is_synced)
        self.assertEqual(len(lyrics.lines), 3)
        self.assertAlmostEqual(lyrics.lines[0].timestamp, 4.5)
        self.assertEqual(lyrics.lines[0].text, "Line 1 lyrics")
        self.assertAlmostEqual(lyrics.lines[1].timestamp, 12.8)
        self.assertEqual(lyrics.lines[2].text, "Line 3 lyrics")

        # Test temporal lookup
        self.assertEqual(lyrics.get_current_index(2.0), 0)
        self.assertEqual(lyrics.get_current_index(5.0), 0)
        self.assertEqual(lyrics.get_current_index(15.0), 1)
        self.assertEqual(lyrics.get_current_index(30.0), 2)

    def test_lrc_parser_multiple_timestamps(self):
        lrc_text = "[00:10.00][00:20.00]Chorus line"
        lyrics = parse_lrc(lrc_text)
        self.assertIsNotNone(lyrics)
        self.assertEqual(len(lyrics.lines), 2)
        self.assertAlmostEqual(lyrics.lines[0].timestamp, 10.0)
        self.assertAlmostEqual(lyrics.lines[1].timestamp, 20.0)
        self.assertEqual(lyrics.lines[0].text, "Chorus line")
        self.assertEqual(lyrics.lines[1].text, "Chorus line")


if __name__ == "__main__":
    unittest.main()
