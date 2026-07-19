from pathlib import Path

import pytest

from scan64.providers.maia import MaiaCheckpoint, MaiaConfig, MaiaConfigurationError


def test_selecting_below_supported_band_discloses_lower_coverage_gap() -> None:
    config = MaiaConfig(
        binary_path=Path("/operator/lc0"),
        checkpoints=(
            MaiaCheckpoint(rating=1100, weights_path=Path("/operator/maia-1100.pb.gz")),
            MaiaCheckpoint(rating=1500, weights_path=Path("/operator/maia-1500.pb.gz")),
        ),
    )

    selection = config.select(900)

    assert selection.checkpoint.rating == 1100
    assert selection.disclosure == (
        "Maia has no checkpoint below 1100; requested rating 900 uses the 1100 checkpoint."
    )


def test_selecting_between_checkpoints_discloses_granularity_limit() -> None:
    config = MaiaConfig(
        binary_path=Path("/operator/lc0"),
        checkpoints=(
            MaiaCheckpoint(rating=1100, weights_path=Path("/operator/maia-1100.pb.gz")),
            MaiaCheckpoint(rating=1500, weights_path=Path("/operator/maia-1500.pb.gz")),
        ),
    )

    selection = config.select(1400)

    assert selection.checkpoint.rating == 1500
    assert selection.disclosure == (
        "Maia checkpoints use approximately 100-Elo granularity; requested rating "
        "1400 uses the nearest 1500 checkpoint."
    )


def test_selecting_exact_checkpoint_has_no_granularity_disclosure() -> None:
    config = MaiaConfig(
        binary_path=Path("/operator/lc0"),
        checkpoints=(
            MaiaCheckpoint(rating=1100, weights_path=Path("/operator/maia-1100.pb.gz")),
            MaiaCheckpoint(rating=1500, weights_path=Path("/operator/maia-1500.pb.gz")),
        ),
    )

    assert config.select(1500).disclosure is None


def test_config_requires_an_absolute_configuration_path() -> None:
    with pytest.raises(MaiaConfigurationError, match="path must be absolute"):
        MaiaConfig.from_toml(Path("maia.toml"))


@pytest.mark.parametrize(
    ("config_text", "error"),
    [
        (
            '[maia]\nbinary_path = "lc0"\n\n[maia.checkpoints]\n'
            '1500 = "/operator/maia-1500.pb.gz"\n',
            "maia.binary_path must be absolute",
        ),
        (
            '[maia]\nbinary_path = "/operator/lc0"\n\n[maia.checkpoints]\n'
            '1500 = "maia-1500.pb.gz"\n',
            "weights path must be absolute",
        ),
    ],
)
def test_config_requires_absolute_operator_paths(
    tmp_path: Path, config_text: str, error: str
) -> None:
    config_path = tmp_path / "maia.toml"
    config_path.write_text(config_text)

    with pytest.raises(MaiaConfigurationError, match=error):
        MaiaConfig.from_toml(config_path)


def test_configured_maia_runtime_fails_closed_when_weights_are_missing(tmp_path: Path) -> None:
    binary_path = tmp_path / "lc0"
    binary_path.touch()
    config = MaiaConfig(
        binary_path=binary_path,
        checkpoints=(
            MaiaCheckpoint(rating=1500, weights_path=tmp_path / "missing-maia-1500.pb.gz"),
        ),
    )

    with pytest.raises(MaiaConfigurationError, match="Maia checkpoint does not exist"):
        config.validate_runtime(config.select(1500))
