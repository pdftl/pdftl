import sys
from unittest.mock import MagicMock, patch

from pdftl.utils.progress import _real_get_track_progress


def test_real_get_track_progress_interactive():
    """
    Covers progress.py lines 11-14:
    Simulates interactive mode to ensure 'rich.progress' is imported and used.
    """
    # Mock rich module
    mock_rich = MagicMock()
    mock_track = MagicMock()
    mock_rich.progress.track = mock_track

    with patch.dict(sys.modules, {"rich": mock_rich, "rich.progress": MagicMock()}):
        tracker = _real_get_track_progress(interactive=True)

        # Verify it returns the track function from rich
        assert tracker == mock_track


def test_real_get_track_progress_non_interactive():
    """
    Covers progress.py lines 15-16:
    Ensures that when interactive=False, the returned tracker
    is a simple identity lambda that does not modify the data.
    """
    tracker = _real_get_track_progress(interactive=False)

    data = [1, 2, 3, 4]

    # The lambda should yield the items exactly as passed
    result = list(tracker(data))
    assert result == data

    # It should also accept kwargs without crashing (like description="Processing")
    result_kwargs = list(tracker(data, description="Doing work"))
    assert result_kwargs == data


def test_get_track_progress_interactive():
    from pdftl.utils.progress import get_track_progress

    tracker = get_track_progress(interactive=True)
    assert tracker([1, 2, 3]) == [1, 2, 3]
