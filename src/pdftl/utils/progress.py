# src/pdftl/utils/progress.py


def get_track_progress(interactive=False):
    # FAKE do nothing stub for now
    return lambda x, **kwargs: x


# TO DO figure out how to use this without polluting output!
def _real_get_track_progress(interactive=False):
    if interactive:
        import rich.progress

        return rich.progress.track

    return lambda x, **kwargs: x
