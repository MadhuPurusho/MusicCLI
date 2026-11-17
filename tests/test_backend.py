"""Comprehensive test suite for Pulse CLI backend engine."""

import math
import time
import unittest
from pathlib import Path

from pulse.audio import AudioEngine, AudioState
from pulse.metadata import TrackMetadata, format_duration, format_file_size
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


if __name__ == "__main__":
    unittest.main()
