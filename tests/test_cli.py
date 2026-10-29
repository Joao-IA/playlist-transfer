import pytest

from music_transfer.cli import main, select
from music_transfer.models import PlaylistRef

REFS = [PlaylistRef("Rock_Foda.csv", "Rock Foda"), PlaylistRef("running.csv", "running")]


@pytest.mark.parametrize("value", ["Rock Foda", "rock_foda", "ROCK_FODA.csv"])
def test_select_by_name_file_name_or_id(value):
    assert select(REFS, [value]) == [REFS[0]]


def test_select_all_by_default():
    assert select(REFS, []) == REFS


def test_select_unknown_playlist():
    with pytest.raises(LookupError):
        select(REFS, ["nope"])


def test_source_and_destination_must_differ(capsys):
    with pytest.raises(SystemExit):
        main(["transfer", "--from", "ytmusic", "--to", "ytmusic"])
    assert "must be different" in capsys.readouterr().err


def test_csv_is_not_a_destination():
    with pytest.raises(SystemExit):
        main(["transfer", "--from", "spotify", "--to", "csv"])


def test_name_needs_a_single_playlist(capsys):
    with pytest.raises(SystemExit):
        main(["transfer", "--from", "csv", "--to", "ytmusic", "--name", "X"])
    assert "--name needs exactly one --playlist" in capsys.readouterr().err


def test_empty_exports_folder_explains_what_to_do(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MUSIC_TRANSFER_HOME", str(tmp_path))
    assert main(["playlists", "csv"]) == 1
    assert "Exportify" in capsys.readouterr().out


def test_transfer_without_login_says_how_to_log_in(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MUSIC_TRANSFER_HOME", str(tmp_path))
    assert main(["transfer", "--from", "csv", "--to", "spotify"]) == 1
    assert "music-transfer login spotify" in capsys.readouterr().out
