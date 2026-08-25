"""The recordings kept on disk between speaking and seeing the words appear.

None of these five behaviours had a test of its own before: they were reachable only by
driving a whole App with a fake microphone, a fake transcriber and a worker thread. Here
they need a folder and nothing else.

The rule every one of them shares: a failure to tidy up is a log line, never an exception.
A dictation must not be lost because a stale file could not be deleted.
"""

import time

from voice_typer import takes


def make_take(directory, number, *, age_days=0.0):
    directory.mkdir(parents=True, exist_ok=True)
    path = takes.path_for(directory, number)
    path.write_bytes(b"RIFF....WAVE")
    if age_days:
        old = time.time() - age_days * takes.SECONDS_PER_DAY
        import os

        os.utime(path, (old, old))
    return path


# ------------------------------------------------------------------------- numbering


def test_a_take_is_named_with_four_digits(tmp_path):
    """Four digits so the names sort in the order they were made — `orphans` relies on it."""
    assert takes.path_for(tmp_path, 7).name == "take-0007.wav"


def test_numbering_continues_above_what_a_previous_session_left(tmp_path):
    make_take(tmp_path, 3)
    make_take(tmp_path, 11)

    assert takes.highest_number(tmp_path) == 11


def test_an_empty_folder_starts_at_zero(tmp_path):
    assert takes.highest_number(tmp_path) == 0


def test_a_folder_that_is_not_there_starts_at_zero(tmp_path):
    """Startup reads this before anything has created the folder."""
    assert takes.highest_number(tmp_path / "never-made") == 0


def test_a_file_that_is_not_a_take_is_ignored(tmp_path):
    make_take(tmp_path, 2)
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "take-nonsense.wav").write_bytes(b"")

    assert takes.highest_number(tmp_path) == 2


# --------------------------------------------------------------------------- orphans


def test_orphans_come_back_oldest_first(tmp_path):
    """Whichever take was interrupted first should be the first one re-sent."""
    make_take(tmp_path, 9)
    make_take(tmp_path, 2)

    assert [p.name for p in takes.orphans(tmp_path)] == ["take-0002.wav", "take-0009.wav"]


def test_a_missing_folder_has_no_orphans(tmp_path):
    assert takes.orphans(tmp_path / "never-made") == []


# ------------------------------------------------------------------- keep and discard


def test_a_take_is_kept_even_when_the_folder_does_not_exist_yet(tmp_path):
    path = tmp_path / "pending" / "take-0001.wav"

    takes.keep_for_retry(b"RIFFdata", path)

    assert path.read_bytes() == b"RIFFdata"


def test_keeping_a_take_where_it_cannot_be_written_does_not_raise(tmp_path):
    """A directory standing where the file should go. The dictation must carry on."""
    path = tmp_path / "take-0001.wav"
    path.mkdir()

    takes.keep_for_retry(b"RIFFdata", path)  # must not raise


def test_a_take_whose_words_arrived_is_removed(tmp_path):
    path = make_take(tmp_path, 1)

    takes.discard(path)

    assert not path.exists()


def test_discarding_a_take_that_is_already_gone_does_not_raise(tmp_path):
    takes.discard(tmp_path / "take-0001.wav")  # must not raise


# ---------------------------------------------------------------------------- pruning


def test_a_recording_older_than_the_limit_is_removed(tmp_path):
    stale = make_take(tmp_path, 1, age_days=30)

    takes.prune_old(tmp_path, 7)

    assert not stale.exists()


def test_a_recent_recording_is_left_alone(tmp_path):
    """An interrupted take is the whole reason the folder exists — pruning must not eat
    one that is still waiting to be re-sent."""
    fresh = make_take(tmp_path, 1)
    stale = make_take(tmp_path, 2, age_days=30)

    takes.prune_old(tmp_path, 7)

    assert fresh.exists()
    assert not stale.exists()


def test_pruning_a_folder_that_is_not_there_does_not_raise(tmp_path):
    takes.prune_old(tmp_path / "never-made", 7)  # must not raise


# ------------------------------------------------------------------ the last resort


def test_the_transcript_is_written_when_the_clipboard_is_unusable(tmp_path):
    """Losing what somebody just said is the worse outcome — this is the one place a
    transcript is deliberately put on disk."""
    logs = tmp_path / "logs"
    path = logs / "last_transcript.txt"

    takes.save_transcript("გამარჯობა", logs, path)

    assert path.read_text(encoding="utf-8") == "გამარჯობა"


def test_a_transcript_that_cannot_be_written_does_not_raise(tmp_path):
    logs = tmp_path / "logs"
    path = logs / "last_transcript.txt"
    path.mkdir(parents=True)

    takes.save_transcript("გამარჯობა", logs, path)  # must not raise
